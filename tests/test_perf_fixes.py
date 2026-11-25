# tests -- remaining perf fixes: tasks.tree rollup in ONE recursive CTE (was O(N^2)
# queries), timeline git-event attribution with git_links loaded once per call
# (was one query per event), and the GUI header render signature with volatile
# per-poll fields (elapsed_min / fractional today_minutes) quantized out.

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, events, projects, tasks, timeline


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home):
    c = db.connect(cfgmod.db_path(home))
    c.row_factory = sqlite3.Row
    db.init_db(c)
    yield c
    c.close()


def _count_statements(conn, needle: str, fn):
    """Run fn() while counting SQL statements containing `needle`."""
    n = {"n": 0}

    def cb(stmt: str) -> None:
        if needle in stmt:
            n["n"] += 1

    conn.set_trace_callback(cb)
    try:
        out = fn()
    finally:
        conn.set_trace_callback(None)
    return out, n["n"]


def test_tree_rollup_is_one_query_and_correct(conn):
    p = projects.create(conn, "P")
    root = tasks.add(conn, p.id, "root")
    prev = root.id
    ids = [root.id]
    for i in range(59):  # chain of 60 tasks: root -> c1 -> ... -> c59
        t = tasks.add(conn, p.id, f"c{i}", parent_id=prev)
        ids.append(t.id)
        prev = t.id
    conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (ids[30],))
    conn.commit()

    roots, n_queries = _count_statements(conn, "FROM tasks", lambda: tasks.tree(conn, project_id=p.id))
    assert len(roots) == 1
    prog = roots[0]["progress"]
    assert prog == {"total": 59, "done": 1, "pct": round(100 * 1 / 59)}  # same semantics as subtask_stats
    assert len(roots[0]["children"]) == 1 and len(roots[0]["children"][0]["children"]) == 1
    # one list() query + one recursive CTE (+ at most a couple of housekeeping
    # statements): NOT one descendants()+COUNT per node (that was 60+ queries)
    assert n_queries <= 4, f"tree() still runs {n_queries} task queries for 60 tasks"


def test_timeline_git_events_resolve_repo_once(conn):
    p = projects.create(conn, "P")
    repo = str((Path.home() / "fake-repo").resolve())
    conn.execute(
        "INSERT INTO git_links(project_id, repo_path, added_at) VALUES (?, ?, ?)",
        (p.id, repo, "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    for i in range(30):
        events.append(conn, "git", f"hash{i}", "commit",
                      {"repo_path": repo, "subject": f"c{i}", "hash": f"hash{i}"})
    rows, n_links = _count_statements(
        conn, "git_links", lambda: timeline.list(conn, project_id=p.id, limit=50))
    assert n_links == 1, f"git_links queried {n_links}x for 30 git events (want 1 preload)"
    git_rows = [r for r in rows if r["entity"] == "git"]
    assert len(git_rows) == 30  # all attributed to the project via the preloaded map


def test_gui_header_signature_quantizes_volatile_fields():
    root = Path(__file__).resolve().parents[1]
    js = (root / "devdash/gui/static/app.js").read_text(encoding="utf-8")
    line = next(l for l in js.splitlines() if 'renderPanel("header"' in l)
    assert "elapsed_min: 0" in line          # per-poll elapsed stays out of the signature
    assert "Math.round(state.status.today_minutes)" in line  # fractional minutes quantized


def test_v11_sort_indexes_exist(home):
    from devdash.core import config, db
    conn = db.connect(config.db_path(home))
    db.init_db(conn)
    idx = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_tasks_sort" in idx and "idx_commits_repo_date_hash" in idx
    assert db.SCHEMA_VERSION == 11
    conn.close()


def test_status_uses_aggregates_not_full_rows(home):
    from devdash.core import config, db, projects as proj, tasks as tsk, stats
    conn = db.connect(config.db_path(home)); db.init_db(conn)
    p = proj.create(conn, "Perf", engine="other")
    proj.set_active(conn, p.id)
    for i in range(5):
        tsk.add(conn, p.id, f"t{i}", description="x" * 5000)
    tsk.set_status(conn, tsk.list(conn, project_id=p.id)[0].id, "in_progress")
    # count/brief helpers agree with the full list but load no descriptions
    assert tsk.count_by_status(conn, p.id, ["todo", "in_progress"]) == 5
    brief = tsk.list_brief(conn, p.id, "in_progress")
    assert len(brief) == 1 and set(brief[0]) == {"id", "title"}
    full = stats.project_progress(conn, project_id=p.id)
    agg = stats.project_progress_sql(conn, p.id)
    assert agg == full  # same shape/values, computed in SQL
    conn.close()


def test_events_payload_capped_and_periodic_prune(home):
    from devdash.core import config, db, events as ev
    conn = db.connect(config.db_path(home)); db.init_db(conn)
    big = {"blob": "z" * 9000}
    e = ev.append(conn, "task", 1, "created", big)
    row = conn.execute("SELECT payload FROM events WHERE id = ?", (e.id,)).fetchone()
    assert len(row["payload"]) <= ev.PAYLOAD_MAX + 2
    for i in range(30):
        ev.append(conn, "task", i, "created", None, commit=False)
    conn.commit()
    assert ev.prune_if_needed(conn, keep=10, every=5) > 0   # 31 events > 10+5 -> prune
    assert conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 10
    assert ev.prune_if_needed(conn, keep=10, every=5) == 0   # below threshold: no-op
    conn.close()


def test_config_load_cache_by_mtime(tmp_path, monkeypatch):
    import json
    from devdash.core import config
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    config._load_cache["key"] = None
    (tmp_path / "config.json").write_text(json.dumps({"lang": "en"}), encoding="utf-8")
    c1 = config.load_config(tmp_path)
    assert c1.lang == "en"
    # same size + forced same mtime -> cache hit, no re-read
    import os
    mt = (tmp_path / "config.json").stat().st_mtime_ns
    (tmp_path / "config.json").write_text(json.dumps({"lang": "fr"}), encoding="utf-8")
    os.utime(tmp_path / "config.json", ns=(mt, mt))
    c2 = config.load_config(tmp_path)
    assert c2 is c1  # cache hit (same object, still lang=en)
    # save_config invalidates/updates the cache
    c1.lang = "fr"
    config.save_config(c1, tmp_path)
    c3 = config.load_config(tmp_path)
    assert c3.lang == "fr"  # save_config refreshed the cache with the saved object
