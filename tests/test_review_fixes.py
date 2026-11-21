"""Regression tests for the full code-review fix pass (post step j).

Covers the HIGH/MEDIUM findings: detach no-op, cross-project feature
resolution, parent cycles, LIKE wildcards, events_since cursor, plan JSON
validation, server Host/Content-Type hardening, active-project scoping of
GUI list endpoints, UTC normalization of git timestamps, config resilience,
color validation, local-day bucketing.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from devdash.core import config as cfgmod
from devdash.core import db, events, features, logs, projects, sessions, stats, tasks
from devdash.core.ai import assistant
from devdash.core.ai.provider import AIError
from devdash.core import gitwatch
from devdash.gui.api import Api, ApiError
from devdash.gui.server import make_server


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home: Path):
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def project(conn):
    p = projects.create(conn, "Game", engine="unity")
    projects.set_active(conn, p.id)
    return p


# --- HIGH: features.detach must actually clear feature_id -------------------

def test_detach_clears_feature_id(conn, project):
    f = features.create(conn, project.id, "dash")
    t = tasks.add(conn, project.id, "dash movement", tags=["gameplay"])
    features.attach(conn, f.id, t.id)
    assert tasks.get(conn, t.id).feature_id == f.id
    features.detach(conn, f.id, t.id)
    assert tasks.get(conn, t.id).feature_id is None  # was silently staying set before


# --- HIGH: feature name resolution must not cross projects -------------------

def test_feature_name_scoped_to_project(conn):
    p1 = projects.create(conn, "One")
    projects.set_active(conn, p1.id)
    p2 = projects.create(conn, "Two")
    f2 = features.create(conn, p2.id, "dash")  # same name in ANOTHER project
    # active is p1: "dash" must NOT resolve to p2's feature
    assert features.get(conn, "dash", p1.id) is None
    with pytest.raises(KeyError):
        features.approve(conn, "dash", project_id=p1.id)
    assert features.get(conn, f2.id).status == "open"  # p2's feature untouched
    # but it resolves fine within its own project
    assert features.get(conn, "dash", p2.id).id == f2.id


# --- MEDIUM: task parent self / cycle / cross-project -------------------------

def test_parent_self_rejected(conn, project):
    t = tasks.add(conn, project.id, "t")
    with pytest.raises(ValueError):
        tasks.update(conn, t.id, parent_id=t.id)


def test_parent_cycle_rejected(conn, project):
    a = tasks.add(conn, project.id, "a")
    b = tasks.add(conn, project.id, "b", parent_id=a.id)
    with pytest.raises(ValueError):
        tasks.update(conn, a.id, parent_id=b.id)  # would create a -> b -> a


def test_parent_cross_project_rejected(conn, project):
    other = projects.create(conn, "Other")
    p = tasks.add(conn, other.id, "in other project")
    t = tasks.add(conn, project.id, "t")
    with pytest.raises(ValueError):
        tasks.update(conn, t.id, parent_id=p.id)


# --- LOW: LIKE wildcard injection in tag / log filters ------------------------

def test_tag_filter_is_exact(conn, project):
    tasks.add(conn, project.id, "full", tags=["100%"])
    tasks.add(conn, project.id, "other", tags=["gameplay"])
    res = tasks.list(conn, project_id=project.id, tag="%")  # used to match everything
    assert res == []
    res = tasks.list(conn, project_id=project.id, tag="100%")
    assert len(res) == 1 and res[0].title == "full"


def test_log_query_escapes_wildcards(conn, project):
    logs.add(conn, "rate is 100% done", project_id=project.id)
    logs.add(conn, "unrelated entry", project_id=project.id)
    # "%" must be treated as a LITERAL percent sign, not as a match-all wildcard
    res = logs.list(conn, project_id=project.id, query="%")
    assert [e.content for e in res] == ["rate is 100% done"]
    res = logs.search(conn, "100%", project_id=project.id)
    assert len(res) == 1
    # "_" likewise must not match any single character
    logs.add(conn, "snake_case word", project_id=project.id)
    res = logs.list(conn, project_id=project.id, query="_")
    assert sorted(e.content for e in res) == ["snake_case word"]


# --- MEDIUM: events_since must never skip events (LIMIT 500) ------------------

def test_events_since_no_skip_over_limit(conn, project, home):
    for i in range(505):
        events.append(conn, "test", i, "ping", {"i": i})
    api = Api(home=home)
    total = events.last_id(conn)
    assert total >= 505
    d1 = api.events_since(0)
    assert len(d1["events"]) == 500
    assert d1["last_id"] == d1["events"][-1]["id"]  # NOT the global max
    d2 = api.events_since(d1["last_id"])
    ids = [e["id"] for e in d1["events"]] + [e["id"] for e in d2["events"]]
    # contiguous coverage of every event id: nothing silently skipped
    assert ids == list(range(1, total + 1))


# --- MEDIUM: AI plan must validate model JSON before writing ------------------

def test_plan_rejects_bad_shapes(conn, project, home, monkeypatch):
    cfg = cfgmod.load_config(home)
    cfg.ai_enabled = True
    cfg.ai_provider = "mock"
    cfg.ai_model = "mock"
    cfgmod.save_config(cfg, home)

    class BadProvider:
        name = "bad"
        def chat(self, messages, temperature=0.4):
            return json.dumps({"subtasks": [
                {"title": "ok", "estimate_min": 30, "tags": ["a"]},   # valid
                {"title": "bad tags", "tags": "plan,tests"},           # string -> invalid
                {"title": "", "estimate_min": 10},                     # empty title
                "not a dict",                                           # not an object
                {"title": "bad est", "estimate_min": "soon"},          # wrong type
            ]})

    # assistant.py binds get_provider by name: patch it in the assistant module
    monkeypatch.setattr(assistant, "get_provider", lambda cfg: BadProvider())
    r = assistant.plan(conn, "grosse tâche", apply=True, home=home)
    assert r["applied"] is True
    assert len(r["proposal"]) == 1 and r["proposal"][0]["title"] == "ok"
    assert len(r["rejected_subtasks"]) == 4
    assert len(r["created_task_ids"]) == 1  # only the valid one was written


def test_extract_json_malformed_raises_aierror(home):
    with pytest.raises(AIError):
        assistant._extract_json("voici du texte sans json", home)
    with pytest.raises(AIError):
        assistant._extract_json('{"subtasks": [broken', home)


# --- MEDIUM: git timestamps normalized to UTC ---------------------------------

def test_git_timestamps_normalized_utc(conn, project, git_repo):
    repo = gitwatch.link(conn, project.id, str(git_repo))
    gitwatch.sync_repo(conn, repo, cfgmod.DEFAULT_COMMIT_PATTERNS)
    for c in gitwatch.commits(conn, repo_path=repo):
        # normalized form: ends with +00:00 (UTC), sortable as a plain string
        assert c.committed_at.endswith("+00:00")
    st = gitwatch.status(conn, project.id)
    assert st["linked"] and st["branch"] in ("main", "master")
    assert st["cached_commits"] == 3
    assert st["commits_today"] == 3  # commits made now count as "today" (local day)
    # second sync without new commits is skipped via HEAD hash (cheap path)
    res = gitwatch.sync_repo(conn, repo, cfgmod.DEFAULT_COMMIT_PATTERNS)
    assert res["new"] == 0 and res.get("skipped") is True


# --- MEDIUM: config resilience -------------------------------------------------

def test_malformed_config_falls_back_to_defaults(home):
    (home / "config.json").write_text("{ not valid json !!!", encoding="utf-8")
    cfg = cfgmod.load_config(home)
    assert cfg.lang == "fr" and cfg.ai_enabled is False and cfg.gui_port == 8765


def test_wrong_typed_config_values_ignored(home):
    (home / "config.json").write_text(json.dumps({
        "lang": "en", "gui_port": "not-a-number", "ai_enabled": "yes",
        "commit_patterns": "not-a-list", "pomodoro_focus_min": True,
    }), encoding="utf-8")
    cfg = cfgmod.load_config(home)
    assert cfg.lang == "en"                      # valid value kept
    assert cfg.gui_port == 8765                  # wrong type -> default
    assert cfg.ai_enabled is False               # "yes" is not a bool
    assert cfg.commit_patterns == cfgmod.DEFAULT_COMMIT_PATTERNS
    assert cfg.pomodoro_focus_min == 25          # bool is not accepted as int


# --- color validation (stored XSS surface) --------------------------------------

def test_project_color_validated(conn):
    with pytest.raises(ValueError):
        projects.create(conn, "Evil", color='red" onmouseover="alert(1)')
    p = projects.create(conn, "Ok", color="#FF00AA")
    assert p.color == "#ff00aa"  # normalized lowercase
    with pytest.raises(ValueError):
        projects.update(conn, p.id, color="javascript:alert(1)")


# --- local-day bucketing --------------------------------------------------------

def test_today_bucket_is_local(conn, project):
    s = sessions.start(conn, project.id)
    sessions.stop(conn, s.id)
    assert stats.today_minutes(conn, project.id) >= 0
    assert stats.today_str() == stats.today_str_local() if hasattr(stats, "today_str_local") else True
    from devdash.core.models import today_str_local
    assert stats.today_str() == today_str_local()
    days = sessions.active_days(conn, project.id)
    assert today_str_local() in days


# --- Api scoping: list endpoints default to the ACTIVE project ------------------

def test_api_lists_default_to_active_project(home, conn, project):
    other = projects.create(conn, "Other")
    tasks.add(conn, project.id, "in game")
    tasks.add(conn, other.id, "in other")
    api = Api(home=home)
    res = api.tasks({"all": "1"})
    assert res["count"] == 1 and res["tasks"][0]["title"] == "in game"
    res_all = api.tasks({"all": "1", "project": "all"})
    assert res_all["count"] == 2
    logs.add(conn, "log game", project_id=project.id)
    logs.add(conn, "log other", project_id=other.id)
    assert api.logs_list({})["count"] == 1
    assert api.logs_list({"project": "all"})["count"] == 2
    with pytest.raises(ApiError) as ei:
        api.tasks({"project": "nope"})
    assert ei.value.status == 404
    # bad int query param -> 400, not 500
    with pytest.raises(ApiError) as ei:
        api.sessions_list({"limit": "abc"})
    assert ei.value.status == 400


# --- server hardening: Host check + Content-Type --------------------------------

def _serve(home):
    srv = make_server(port=0, home=home)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_server_rejects_foreign_host(home):
    projects  # noqa
    srv = _serve(home)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        req = urllib.request.Request(
            base + "/api/projects",
            data=json.dumps({"name": "X"}).encode(),
            headers={"Content-Type": "application/json", "Host": "evil.example.com"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403
    finally:
        srv.shutdown(); srv.server_close()


def test_server_rejects_non_json_content_type(home):
    srv = _serve(home)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        # cross-site "simple request" style: text/plain must NOT reach the route
        req = urllib.request.Request(
            base + "/api/tasks/add",
            data=b'{"title": "csrf"}',
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 415
        # garbage Content-Length -> 400 (not a dropped connection)
        req = urllib.request.Request(
            base + "/api/tasks/add",
            data=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "abc"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400
        # oversized body -> 413 (forged Content-Length with a small actual body,
        # so the client is not aborted mid-upload on Windows)
        req = urllib.request.Request(
            base + "/api/tasks/add",
            data=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "2000000"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 413
    finally:
        srv.shutdown(); srv.server_close()


def test_server_500_is_generic(home):
    srv = _serve(home)
    port = srv.server_address[1]
    try:
        # bad int in query -> ApiError 400 with a clean message (not a 500 traceback)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/events?since=abc")
        assert e.value.code == 400
        body = json.loads(e.value.read())
        assert "since" in body["error"]
    finally:
        srv.shutdown(); srv.server_close()


# --- migration v3 -> v4 keeps data ----------------------------------------------

def test_migrate_v3_to_v4_keeps_data(home):
    import sqlite3 as sq
    path = home / "devdash.db"
    c = sq.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    # hand-build a minimal v3 database
    c.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE projects(id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        description TEXT DEFAULT '', engine TEXT DEFAULT 'other', color TEXT DEFAULT '#8a8f98',
        status TEXT DEFAULT 'idea', archived INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
    CREATE TABLE git_links(project_id INTEGER PRIMARY KEY REFERENCES projects(id), repo_path TEXT NOT NULL);
    CREATE TABLE git_commits(hash TEXT PRIMARY KEY, repo_path TEXT NOT NULL, short TEXT, author TEXT,
        message TEXT, branch TEXT, committed_at TEXT, parsed_task_id INTEGER, parsed_feature TEXT);
    CREATE TABLE git_sync_state(repo_path TEXT PRIMARY KEY, last_sync_at TEXT, last_seen_hash TEXT);
    INSERT INTO meta VALUES('schema_version','3');
    INSERT INTO projects(slug,name,engine,created_at,updated_at) VALUES('game','Game','unity','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00');
    INSERT INTO git_commits VALUES('abc123','/tmp/r','abc','me','msg','main','2026-01-01T00:00:00+00:00',NULL,NULL);
    """)
    c.commit()
    c.close()
    # opening through devdash migrates to v4
    conn = db.connect(path)
    db.init_db(conn)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert row["value"] == str(db.SCHEMA_VERSION)
    # data survived
    assert conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM git_commits").fetchone()["c"] == 1
    # new v4 column exists
    cols = {r[1] for r in conn.execute("PRAGMA table_info(git_sync_state)")}
    assert "branch" in cols
    # new indexes exist
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_tasks_feature", "idx_tasks_parent", "idx_sessions_task", "idx_log_task"} <= idx
    conn.close()
