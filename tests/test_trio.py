# tests -- timeline feed, estimate-vs-actual, period report (the "trio" features)
# Works on Windows, Mac and Linux.

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, events, gitwatch, logs, projects as proj, report
from devdash.core import sessions as sess, stats, tasks as tsk, timeline as tl
from devdash.gui import server
from devdash.gui.api import Api, ApiError
from devdash.mcp_server import TOOLS, _call


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home):
    c = db.connect(cfgmod.db_path(home))
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def api(home) -> Api:
    a = Api(home=home)
    a.projects_create({"name": "Game", "engine": "unity", "set_active": True})
    return a


def _session(conn, pid, task_id, minutes, days_ago=0):
    """Insert a STOPPED session with an exact duration (deterministic: real-time
    start/stop would give ~0 min)."""
    end = datetime.now(timezone.utc) - timedelta(days=days_ago)
    start = end - timedelta(minutes=minutes)
    conn.execute(
        "INSERT INTO sessions(project_id, task_id, kind, started_at, ended_at, duration_min, note) "
        "VALUES (?, ?, 'focus', ?, ?, ?, '')",
        (pid, task_id, start.isoformat(), end.isoformat(), float(minutes)),
    )
    conn.commit()


# --- timeline ---------------------------------------------------------------

def test_timeline_list_order_filter_scope(conn):
    p1 = proj.create(conn, "One")
    p2 = proj.create(conn, "Two")
    t1 = tsk.add(conn, p1.id, "task in one")
    t2 = tsk.add(conn, p2.id, "task in two")
    logs.add(conn, "log in one", project_id=p1.id)
    tsk.set_status(conn, t1.id, "done")

    all_rows = tl.list(conn, limit=50)
    assert all_rows[0]["id"] > all_rows[-1]["id"]  # newest first
    entities = {r["entity"] for r in all_rows}
    assert {"project", "task", "log"} <= entities

    only_tasks = tl.list(conn, entity="task", limit=50)
    assert only_tasks and all(r["entity"] == "task" for r in only_tasks)

    scoped = tl.list(conn, project_id=p1.id, limit=50)
    assert scoped
    # project scoping: p2's task event must NOT appear in p1's feed
    ids = {r["entity_id"] for r in scoped if r["entity"] == "task"}
    assert str(t1.id) in ids and str(t2.id) not in ids

    with pytest.raises(ValueError):
        tl.list(conn, entity="nope")


def test_timeline_labels_and_detail(conn):
    p = proj.create(conn, "One")
    t = tsk.add(conn, p.id, "Fix the dash")
    tsk.set_status(conn, t.id, "done")
    rows = tl.list(conn, project_id=p.id, entity="task")
    labels = {tl.label(r) for r in rows}
    assert any("créée" in x or "created" in x for x in labels)  # lang-dependent, just non-empty
    done_row = next(r for r in rows if r["action"] == "status:done")
    assert tl.label(done_row) == "Terminée" or tl.label(done_row) == "Done"
    assert "Fix the dash" in tl.detail(done_row)
    for r in rows:
        assert r["icon"] == "📝"


def test_timeline_git_events_attributed(conn, git_repo):
    p = proj.create(conn, "One")
    repo = gitwatch.link(conn, p.id, str(git_repo))
    gitwatch.sync_repo(conn, repo, cfgmod.load_config(home=None).commit_patterns)
    rows = tl.list(conn, project_id=p.id, entity="git", limit=20)
    assert any(r["action"] == "commit" for r in rows)
    assert any(r["action"] == "linked" for r in rows)
    # the commit event resolves to the project through the linked repo path
    commit_row = next(r for r in rows if r["action"] == "commit")
    from devdash.core.models import Event
    ev = Event(id=commit_row["id"], entity="git", entity_id=commit_row["entity_id"],
               action="commit", payload=commit_row["payload"], created_at=commit_row["created_at"])
    assert tl.event_project(conn, ev) == p.id


# --- estimates ---------------------------------------------------------------

def test_estimates_per_task_and_agg(conn):
    p = proj.create(conn, "One")
    t1 = tsk.add(conn, p.id, "estimated", estimate_min=60)
    t2 = tsk.add(conn, p.id, "no estimate")
    _session(conn, p.id, t1.id, 30)
    _session(conn, p.id, t1.id, 45)
    _session(conn, p.id, t2.id, 20)
    out = stats.estimates(conn, p.id)
    by_id = {r["id"]: r for r in out["tasks"]}
    assert by_id[t1.id]["actual_min"] == 75.0
    assert by_id[t1.id]["delta_min"] == 15.0
    assert by_id[t1.id]["ratio"] == 1.25
    assert by_id[t2.id]["estimate_min"] is None and by_id[t2.id]["ratio"] is None
    a = out["agg"]
    assert a["with_estimate"] == 1 and a["est_total_min"] == 60
    assert a["actual_on_estimated_min"] == 75.0
    assert a["pct_of_estimate"] == 125.0
    assert a["over"] == 1 and a["under"] == 0
    assert a["actual_total_min"] == 95.0
    # overruns first
    assert out["tasks"][0]["id"] == t1.id


def test_estimates_empty(conn):
    p = proj.create(conn, "One")
    out = stats.estimates(conn, p.id)
    assert out["tasks"] == []
    assert out["agg"]["pct_of_estimate"] is None


# --- report ------------------------------------------------------------------

def test_report_week_markdown(conn):
    p = proj.create(conn, "Game", engine="unity")
    t = tsk.add(conn, p.id, "Ship v0.8", estimate_min=60)
    _session(conn, p.id, t.id, 90)
    tsk.set_status(conn, t.id, "done")
    logs.add(conn, "Bloqué sur les i-frames, résolu avec un coyote time", project_id=p.id)
    out = report.period_report(conn, p.id, period="week")
    md = out["markdown"]
    assert out["project"] == p.slug and out["period"] == "week" and out["days"] == 7
    assert md.startswith("# ") and p.name in md
    for section in ("## ",):
        assert section in md
    assert "Ship v0.8" in md          # done task listed
    assert "coyote" in md             # devlog entry included
    assert "1h30" in md               # 90 session minutes appear (fmt_min)
    assert out["ai"] is False


def test_report_excludes_old_data_and_validates(conn):
    p = proj.create(conn, "Game")
    t = tsk.add(conn, p.id, "ancient task")
    tsk.set_status(conn, t.id, "done")
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    conn.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (old, t.id))
    conn.commit()
    out = report.period_report(conn, p.id, period="week")
    assert "ancient task" not in out["markdown"]
    with pytest.raises(ValueError):
        report.period_report(conn, p.id, period="year")
    with pytest.raises(KeyError):
        report.period_report(conn, 999)


def test_report_ai_failure_still_returns_report(conn, monkeypatch):
    p = proj.create(conn, "Game")
    def boom(*a, **k):
        raise RuntimeError("no provider")
    import devdash.core.ai.assistant as assistant
    monkeypatch.setattr(assistant, "summarize_week", boom)
    out = report.period_report(conn, p.id, period="week", ai=True)
    assert out["ai"] is False
    assert "no provider" in out["markdown"]


# --- API ---------------------------------------------------------------------

def test_api_routes_registered():
    assert "/api/timeline" in server.GET_ROUTES
    assert "/api/stats/estimates" in server.GET_ROUTES
    assert "/api/report" in server.POST_ROUTES


def test_api_timeline_estimates_report(api: Api):
    api.tasks_add({"title": "demo task", "estimate_min": 45})
    r = api.timeline({})
    assert r["count"] >= 1 and all("icon" in e for e in r["events"])
    r2 = api.timeline({"type": "task"})
    assert all(e["entity"] == "task" for e in r2["events"])
    with pytest.raises(ApiError):
        api.timeline({"type": "bogus"})
    est = api.stats_estimates({})
    assert est["agg"]["with_estimate"] == 1
    rep = api.report({})
    assert rep["markdown"].startswith("# ") and rep["project"] == "game"
    with pytest.raises(ApiError):
        api.report({"period": "year"})


# --- MCP ---------------------------------------------------------------------

def test_mcp_timeline_and_report(home, conn):
    p = proj.create(conn, "Game")
    proj.set_active(conn, p.id)
    tsk.add(conn, p.id, "mcp task")
    names = {t["name"] for t in TOOLS}
    assert "devdash_timeline" in names and "devdash_report" in names
    r = _call("devdash_timeline", {"limit": 10}, home)
    assert r["count"] >= 1
    r2 = _call("devdash_report", {"period": "week"}, home)
    assert "markdown" in r2 and "Game" in r2["markdown"]
    with pytest.raises(KeyError):
        _call("devdash_timeline", {"entity": "bogus"}, home)
    with pytest.raises(KeyError):
        _call("devdash_report", {"period": "year"}, home)
