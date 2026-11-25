# tests -- audit fixes
# Works on Windows, Mac and Linux.

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
    f = feats.delete(conn, fid, p.id, permanent=True)  # hard delete (default is soft/trash)
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
    # delete via API detaches the task (permanent; default is soft/trash)
    r = api.features_delete({"ref": "air-dash", "permanent": True})
    assert r["deleted"] == f["id"]
    t2 = next(x for x in api.tasks({"q": "task-1"})["tasks"] if x["id"] == t["id"])
    assert t2["feature_id"] is None
    # deleting again -> 404
    with pytest.raises(ApiError) as e:
        api.features_delete({"ref": "air-dash"})
    assert e.value.status == 404


# --- native folder picker (POST /api/fs/pick) ----------------------------------


def test_folder_picker_command_per_platform():
    from devdash.gui.api import _folder_picker_command
    w = _folder_picker_command("t", platform="win32")
    assert w and w[0] == "powershell" and any("FolderBrowserDialog" in p for p in w)
    m = _folder_picker_command("t", platform="darwin")
    assert m and m[0] == "osascript" and any("choose folder" in p for p in m)
    ln = _folder_picker_command("t", platform="linux")
    assert ln is None or ln[0] == "zenity"  # depends on zenity being installed


def test_api_fs_pick_directory(monkeypatch, api):
    from devdash.gui import api as apimod

    class _R:
        def __init__(self, stdout):
            self.stdout = stdout
            self.returncode = 0

    monkeypatch.setattr(apimod.subprocess, "run", lambda *a, **k: _R("/home/danny/Games/aether-vale\n"))
    # CI runners (Linux) have no zenity installed: pretend it exists
    monkeypatch.setattr(apimod.shutil, "which", lambda c: f"/usr/bin/{c}")
    assert api.fs_pick_directory({"title": "x"}) == {"path": "/home/danny/Games/aether-vale"}
    monkeypatch.setattr(apimod.subprocess, "run", lambda *a, **k: _R(""))
    assert api.fs_pick_directory({}) == {"path": None}  # cancelled dialog
    with pytest.raises(ApiError) as e:
        api.fs_pick_directory({"title": "x" * 200})
    assert e.value.status == 400


def test_fs_pick_route_registered():
    from devdash.gui import server
    assert "/api/fs/pick" in server.POST_ROUTES


# --- AI project analysis -> ticket proposals (creation needs explicit consent) --


def test_feature_flags_include_suggest():
    from devdash.core.ai import FEATURE_FLAGS
    from devdash.core.config import Config
    assert "suggest" in FEATURE_FLAGS
    assert Config().ai_feature_flags.get("suggest") is True


def test_api_ai_suggest_then_apply(api, home):
    import json as _json
    (home / "config.json").write_text(_json.dumps(
        {"ai_enabled": True, "ai_provider": "mock", "ai_model": "mock"}), encoding="utf-8")
    r = api.ai_suggest({})
    assert len(r["proposals"]) == 3  # read-only: proposes only, never writes
    r2 = api.ai_suggest_apply({"tasks": r["proposals"]})  # explicit consent path
    assert r2["count"] == 3 and len(r2["created"]) == 3
    with pytest.raises(ApiError) as e:
        api.ai_suggest_apply({"tasks": [{"title": "x", "priority": "ultra"}]})
    assert e.value.status == 400
    with pytest.raises(ApiError):
        api.ai_suggest_apply({"tasks": "nope"})
    with pytest.raises(ApiError):
        api.ai_suggest_apply({"tasks": [{"title": f"t{i}"} for i in range(25)]})


def test_ai_suggest_routes_registered():
    from devdash.gui import server
    assert "/api/ai/suggest" in server.POST_ROUTES
    assert "/api/ai/suggest/apply" in server.POST_ROUTES


def test_gui_server_falls_back_when_port_busy(home):
    # a busy port (stale instance / other service) must not kill the GUI:
    # _bind_server falls back to a free port and warns
    import threading
    from devdash.gui.desktop import _bind_server
    from devdash.gui.server import make_server
    srv1 = make_server(port=0, home=home)
    busy_port = srv1.server_address[1]
    threading.Thread(target=srv1.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
    srv2, warn = _bind_server(busy_port, home=home)
    assert srv2.server_address[1] != busy_port and warn and "in use" in warn
    srv2.server_close()          # never served: shutdown() would block forever
    srv1.shutdown(); srv1.server_close()


# --- light/dark theme setting ---------------------------------------------------


def test_theme_setting_validated_and_saved(api, home):
    from devdash.core import config as cfgmod
    assert api.settings_get()["general"]["theme"] == "auto"  # default follows the OS
    assert api.settings_save({"theme": "auto"})["settings"]["general"]["theme"] == "auto"
    r = api.settings_save({"theme": "light"})
    assert r["settings"]["general"]["theme"] == "light"
    assert cfgmod.load_config(home).theme == "light"  # persisted in config.json
    with pytest.raises(ApiError) as e:
        api.settings_save({"theme": "neon"})
    assert e.value.status == 400


# --- read-only git runner --------------------------------------------------------

def _make_repo(tmp_path: Path) -> Path:
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"PATH": __import__("os").environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": str(tmp_path)}
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True, env=env)
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, env=env)
    return repo


def test_git_run_readonly_allowlist(tmp_path):
    repo = _make_repo(tmp_path)
    ok = gitwatch.run_readonly(str(repo), "git log --oneline")
    assert ok["ok"] and "init" in ok["stdout"]
    ok = gitwatch.run_readonly(str(repo), "status --short")
    assert ok["ok"]
    ok = gitwatch.run_readonly(str(repo), "branch -a")
    assert ok["ok"]
    # every write / escape attempt must come back ok=false, never executed
    refused = [
        "git push",
        "git commit -m x",
        "git branch -d main",
        "git branch newb",            # positional arg would create a ref
        "git tag v1",                 # same for tags
        "git diff --output=/tmp/evil",
        "git -C /etc status",
        "log --exec=evil",
        "status; rm -rf /",
        "remote add origin http://x",
        "", "   ",
        "x" * 600,
    ]
    for cmd in refused:
        r = gitwatch.run_readonly(str(repo), cmd)
        assert not r["ok"], cmd
        assert r["stderr"], cmd
    # shell metacharacters are inert: no shell means they are just inert argv
    # tokens for git — nothing is executed and nothing is written
    for cmd in ["status | cat", "status > out.txt", "status && echo pwned", "status `id`"]:
        gitwatch.run_readonly(str(repo), cmd)
    assert not (repo / "out.txt").exists()
    assert sorted(p.name for p in repo.iterdir() if p.name != ".git") == ["a.txt"]
    # repo untouched: still exactly one commit, no extra refs
    r = gitwatch.run_readonly(str(repo), "log --oneline")
    assert len([l for l in r["stdout"].splitlines() if l.strip()]) == 1
    r = gitwatch.run_readonly(str(repo), "tag")
    assert r["stdout"].strip() == ""


def test_api_git_run(api, tmp_path):
    from devdash.gui import server
    assert "/api/git/run" in server.POST_ROUTES
    # no linked repo -> 400
    with pytest.raises(ApiError) as e:
        api.git_run({"cmd": "status"})
    assert e.value.status == 400
    with pytest.raises(ApiError) as e:
        api.git_run({})  # missing cmd
    assert e.value.status == 400
    repo = _make_repo(tmp_path)
    api.git_link({"path": str(repo)})
    r = api.git_run({"cmd": "git rev-parse --is-inside-work-tree"})
    assert r["ok"] and r["stdout"].strip() == "true"
    assert r["project"] == "game" and r["repo"] == str(repo)
    r = api.git_run({"cmd": "git push"})
    assert not r["ok"] and "not allowed" in r["stderr"]


# --- git "GitHub Desktop" views: changes / branches / open (read-only) --------

def test_parse_porcelain_and_numstat():
    text = ' M src/main.py\nA  new.txt\n?? scratch.md\nR  "old name.txt" -> "new name.txt"\n'
    rows = gitwatch.parse_porcelain(text)
    assert [r["path"] for r in rows] == ["src/main.py", "new.txt", "scratch.md", "new name.txt"]
    assert rows[0]["kind"] == "modified" and rows[0]["staged"] is False
    assert rows[1]["kind"] == "added" and rows[1]["staged"] is True
    assert rows[2]["kind"] == "untracked" and rows[2]["staged"] is False
    assert rows[3]["kind"] == "renamed"
    num = gitwatch.parse_numstat("3\t1\tsrc/main.py\n-\t-\tbin.dat\n10\t0\tnew.txt\n")
    assert num["src/main.py"] == (3, 1) and num["bin.dat"] == (0, 0) and num["new.txt"] == (10, 0)


def test_gitwatch_changes_and_branches(tmp_path):
    repo = _make_repo(tmp_path)
    # one modified tracked file + one untracked file
    (repo / "a.txt").write_text("hello world\n", encoding="utf-8")
    (repo / "scratch.md").write_text("x\n", encoding="utf-8")
    ch = gitwatch.changes(str(repo))
    assert ch["ok"] and ch["count"] == 2
    by_path = {f["path"]: f for f in ch["files"]}
    # "hello\n" -> "hello world\n": one line replaced = 1 insertion + 1 deletion
    assert by_path["a.txt"]["kind"] == "modified"
    assert by_path["a.txt"]["added"] == 1 and by_path["a.txt"]["deleted"] == 1
    assert by_path["scratch.md"]["kind"] == "untracked"
    br = gitwatch.branches(str(repo))
    assert br["ok"] and br["current"] in {b["name"] for b in br["branches"]}
    cur = [b for b in br["branches"] if b["current"]]
    assert len(cur) == 1 and cur[0]["short"] and cur[0]["subject"] == "init"


def test_api_git_changes_branches_open(api, tmp_path, monkeypatch):
    from devdash.gui import server
    for route in ("/api/git/changes", "/api/git/branches", "/api/git/open"):
        assert route in server.POST_ROUTES
    # no linked repo -> clean 400 on all three
    for fn in (api.git_changes, api.git_branches, api.git_open):
        with pytest.raises(ApiError) as e:
            fn({})
        assert e.value.status == 400
    repo = _make_repo(tmp_path)
    api.git_link({"path": str(repo)})
    ch = api.git_changes({})
    assert ch["ok"] and ch["repo"] == str(repo) and ch["project"] == "game"
    br = api.git_branches({})
    assert br["ok"] and br["current"]
    # git_open must invoke the OS opener on the LINKED repo only (no shell)
    calls = []
    monkeypatch.setattr("devdash.gui.api.subprocess.Popen",
                        lambda cmd, **kw: calls.append(cmd))
    r = api.git_open({})
    assert r["opened"] == str(repo)
    assert len(calls) == 1 and calls[0][-1] == str(repo) and isinstance(calls[0], list)


def test_api_git_changes_linked_task(api, tmp_path, conn):
    repo = _make_repo(tmp_path)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")  # uncommitted change
    api.git_link({"path": str(repo)})
    # no session, no parsed commit -> no linked task
    ch = api.git_changes({})
    assert ch["ok"] and ch["count"] >= 1 and ch["linked_task"] is None
    # running session on a task -> source "session"
    t = api.tasks_add({"title": "Fix HUD"})
    api.session_start({"task_id": t["id"]})
    ch = api.git_changes({})
    assert ch["linked_task"] == {"id": t["id"], "title": "Fix HUD", "status": "todo", "source": "session"}
    api.session_stop({})
    # no session -> fall back to the last commit's parsed task (#N in the message)
    from devdash.core.models import now_iso
    conn.execute(
        "INSERT INTO git_sync_state(repo_path, last_sync_at, last_seen_hash, branch) VALUES (?, ?, ?, 'main')",
        (str(repo), now_iso(), "b" * 40))
    conn.execute(
        "INSERT INTO git_commits(repo_path, hash, short, author, message, branch, committed_at, parsed_task_id) "
        "VALUES (?, ?, 'def5678', 'me', 'fix: HUD #' || ?, 'main', ?, ?)",
        (str(repo), "c" * 40, t["id"], now_iso(), t["id"]))
    conn.commit()
    ch = api.git_changes({})
    lt = ch["linked_task"]
    assert lt and lt["id"] == t["id"] and lt["source"] == "commit" and lt["commit"] == "def5678"
