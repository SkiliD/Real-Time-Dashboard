"""Tests for the 2026-11 audit fixes: input validation (400 not 500), core guards,
SQLite busy_timeout, i18n duplicate keys, ReDoS pattern rejection, cross-project
attach, atomic sessions, MCP bounds."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, events, features as feats, logs, projects as proj, sessions as sess, tasks as tsk
from devdash.core import gitwatch
from devdash.gui.api import Api, ApiError


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


# --- core validation ---------------------------------------------------------

def test_project_name_required(conn):
    with pytest.raises(ValueError):
        proj.create(conn, "")
    with pytest.raises(ValueError):
        proj.create(conn, "   ")
    with pytest.raises(ValueError):
        proj.create(conn, 123)
    p = proj.create(conn, "  Ok  ")
    assert p.name == "Ok"


def test_project_update_name_empty_rejected(conn):
    p = proj.create(conn, "A")
    with pytest.raises(ValueError):
        proj.update(conn, p.id, name="  ")
    # no-op update emits no event
    before = events.last_id(conn)
    proj.update(conn, p.id)  # nothing to change
    assert events.last_id(conn) == before


def test_task_title_required_and_parent_validated(conn):
    p = proj.create(conn, "A")
    with pytest.raises(ValueError):
        tsk.add(conn, p.id, "")
    with pytest.raises(ValueError):
        tsk.add(conn, p.id, "   ")
    with pytest.raises(ValueError):
        tsk.add(conn, p.id, "x", estimate_min=-5)
    with pytest.raises(KeyError):
        tsk.add(conn, p.id, "x", parent_id=999)          # unknown parent
    # cross-project parent rejected
    p2 = proj.create(conn, "B")
    parent = tsk.add(conn, p2.id, "parent-in-B")
    with pytest.raises(ValueError):
        tsk.add(conn, p.id, "child", parent_id=parent.id)
    # cross-project feature rejected
    f2 = feats.create(conn, p2.id, "featB")
    with pytest.raises(ValueError):
        tsk.add(conn, p.id, "x", feature_id=f2.id)
    t = tsk.add(conn, p.id, "ok")
    assert t.title == "ok"


def test_feature_attach_cross_project_rejected(conn):
    a = proj.create(conn, "A")
    b = proj.create(conn, "B")
    f = feats.create(conn, a.id, "dash")
    t = tsk.add(conn, b.id, "task-of-B")
    with pytest.raises(ValueError):
        feats.attach(conn, f.id, t.id)


def test_sessions_stop_idempotent(conn):
    p = proj.create(conn, "A")
    s = sess.start(conn, p.id)
    sess.stop(conn, s.id, note="first")
    with pytest.raises(ValueError):
        sess.stop(conn, s.id, note="second")  # already stopped: no overwrite
    s2 = sess.get(conn, s.id)
    assert s2.note == "first"


def test_sessions_start_single_started_event(conn):
    p = proj.create(conn, "A")
    before = events.last_id(conn)
    sess.start(conn, p.id)
    evs = events.since(conn, before)
    started = [e for e in evs if e.entity == "session" and e.action == "started"]
    assert len(started) == 1  # not two


def test_sessions_minutes_bounded(conn):
    p = proj.create(conn, "A")
    with pytest.raises(ValueError):
        sess.start(conn, p.id, minutes=-5)
    with pytest.raises(ValueError):
        sess.start(conn, p.id, minutes=99999)


def test_event_from_row_corrupt_payload(home):
    c = db.connect(cfgmod.db_path(home))
    db.init_db(c)
    c.execute("INSERT INTO events(entity, action, payload, created_at) VALUES ('x','y','{corrupt', '2026-01-01T00:00:00+00:00')")
    c.commit()
    evs = events.since(c, 0)
    assert evs[-1].payload == {}  # never crash readers
    c.close()


def test_db_busy_timeout_set(home):
    c = db.connect(cfgmod.db_path(home))
    val = c.execute("PRAGMA busy_timeout").fetchone()[0]
    assert val >= 1000
    c.close()


# --- config: ReDoS pattern rejection -----------------------------------------

def test_nested_quantifier_pattern_rejected(home):
    cfg = cfgmod.load_config(home)
    with pytest.raises(ValueError):
        cfgmod.apply_general_settings(cfg, {"commit_patterns": ["(a+)+$"]})
    with pytest.raises(ValueError):
        cfgmod.apply_general_settings(cfg, {"commit_patterns": ["(x|y)*{2,}"]})
    # a normal pattern still passes
    cfgmod.apply_general_settings(cfg, {"commit_patterns": [r"#(\d+)"]})
    # and parse_message refuses a hand-edited dangerous pattern
    with pytest.raises(gitwatch.GitError):
        gitwatch.parse_message("fix #1", ["(a+)+$"])


# --- i18n: no duplicate keys ---------------------------------------------------

def test_i18n_no_duplicate_keys():
    from devdash.core import i18n
    import inspect, re
    src = inspect.getsource(i18n)
    for lang in ("fr", "en"):
        block = src.split(f'{lang.upper()}: dict')[1]
        block = block.split("\n\n")[0]
        keys = re.findall(r'^\s{4}"([^"]+)":', block, re.M)
        dups = {k for k in keys if keys.count(k) > 1}
        assert not dups, f"duplicate i18n keys in {lang}: {dups}"


def test_ai_feature_flags_include_task_verdict():
    from devdash.core.ai import FEATURE_FLAGS
    assert "task_verdict" in FEATURE_FLAGS
    assert set(FEATURE_FLAGS) == set(cfgmod.Config().ai_feature_flags)


# --- API: wrong types are 400, never 500 --------------------------------------

def test_api_wrong_types_are_400(api):
    with pytest.raises(ApiError) as e: api.projects_create({"name": 123})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.projects_create({})
    assert e.value.status == 400 and "name" in str(e.value)
    with pytest.raises(ApiError) as e: api.projects_create({"name": "   "})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.tasks_add({"title": 123})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.tasks_add({"title": "x", "tags": "abc"})  # string not split
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.logs_add({"content": 5})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.logs_add({"content": "x", "tags": "abc"})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.features_add({"name": 9})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.ai_ask({"question": 3})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.git_link({"path": 42})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.detect_path({"path": 5})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.events_since("-1")
    assert e.value.status == 400
    with pytest.raises(ApiError) as e: api.session_start({"minutes": -3})
    assert e.value.status == 400


def test_api_tasks_update_parent_error_not_masked(api):
    t = api.tasks_add({"title": "x"})
    with pytest.raises(ApiError) as e:
        api.tasks_update({"id": t["id"], "parent_id": 999})
    assert e.value.status == 404
    assert "parent" in str(e.value)  # "no parent task #999", not "no task #x"


def test_api_git_ignore_confined_to_linked_repo(api, home, tmp_path):
    # no linked repo -> 400
    with pytest.raises(ApiError) as e:
        api.git_ignore({})
    assert e.value.status == 400
    # link a real repo, then a foreign repo path must be refused
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    api.git_link({"path": str(repo)})
    with pytest.raises(ApiError) as e:
        api.git_ignore({"repo": str(tmp_path)})  # not the linked repo
    assert e.value.status == 400
    r = api.git_ignore({})  # linked repo: works
    assert r["updated"] or r.get("already_present")


def test_api_status_has_done_today_and_git(api):
    s = api.status()
    assert "done_today" in s and "git" in s


def test_api_features_attach_cross_project_400(api):
    api.projects_create({"name": "Other"})
    f = api.features_add({"name": "dash"})  # in active project "Game"
    t_other = api.tasks_add({"title": "t", "project": "other"})
    with pytest.raises(ApiError) as e:
        api.features_attach({"ref": "dash", "task_id": t_other["id"]})
    assert e.value.status == 400
    assert "belongs to project" in str(e.value)


# --- features: update / delete ------------------------------------------------


def test_feature_update_rename_and_uniqueness(conn):
    p = proj.create(conn, "A")
    f1 = feats.create(conn, p.id, "dash")
    feats.create(conn, p.id, "jump")
    # rename works + appends exactly one event
    before = events.last_id(conn)
    f1 = feats.update(conn, f1.id, p.id, name="air-dash", description="d")
    assert f1.name == "air-dash" and f1.description == "d"
    evs = [e for e in events.since(conn, before) if e.entity == "feature" and e.action == "updated"]
    assert len(evs) == 1
    # duplicate name in the same project rejected
    with pytest.raises(ValueError):
        feats.update(conn, f1.id, p.id, name="jump")
    # empty name rejected
    with pytest.raises(ValueError):
        feats.update(conn, f1.id, p.id, name="   ")
    # unknown feature
    with pytest.raises(KeyError):
        feats.update(conn, 9999, p.id, name="x")
    # same name allowed in another project
    p2 = proj.create(conn, "B")
    f2 = feats.create(conn, p2.id, "other")
    f2 = feats.update(conn, f2.id, p2.id, name="air-dash")
    assert f2.name == "air-dash"


def test_feature_delete_detaches_tasks_and_cleans_events(conn):
    p = proj.create(conn, "A")
    f = feats.create(conn, p.id, "dash")
    t = tsk.add(conn, p.id, "task-1")
    feats.attach(conn, f.id, t.id)
    fid = f.id
    f = feats.delete(conn, fid, p.id)
    assert f.name == "dash"
    # feature gone
    assert feats.get(conn, fid) is None
    # task survives, detached
    t2 = tsk.get(conn, t.id)
    assert t2 is not None and t2.feature_id is None
    # old feature events cleaned, only the "deleted" marker remains
    evs = [e for e in events.since(conn, 0) if e.entity == "feature" and e.entity_id == str(fid)]
    assert [e.action for e in evs] == ["deleted"]
    # deleting again -> KeyError
    with pytest.raises(KeyError):
        feats.delete(conn, fid, p.id)


def test_api_features_update_and_delete(api):
    f = api.features_add({"name": "dash"})
    api.features_add({"name": "jump"})
    t = api.tasks_add({"title": "task-1"})
    api.features_attach({"ref": "dash", "task_id": t["id"]})
    # rename via API
    r = api.features_update({"ref": "dash", "name": "air-dash"})
    assert r["name"] == "air-dash"
    # duplicate name -> 400 (not 500)
    with pytest.raises(ApiError) as e:
        api.features_update({"ref": "air-dash", "name": "jump"})
    assert e.value.status == 400
    # delete via API detaches the task
    r = api.features_delete({"ref": "air-dash"})
    assert r["deleted"] == f["id"]
    t2 = next(x for x in api.tasks({"q": "task-1"})["tasks"] if x["id"] == t["id"])
    assert t2["feature_id"] is None
    # deleting again -> 404
    with pytest.raises(ApiError) as e:
        api.features_delete({"ref": "air-dash"})
    assert e.value.status == 404
