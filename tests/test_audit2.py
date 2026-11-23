from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.cli import deps
from devdash.core import config, db, events, projects, sessions, stats, tasks
from devdash.core.gitwatch import GitError

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def conn(home: Path):
    c = db.connect(config.db_path(home))
    db.init_db(c)
    return c


# --- CLI: task edit must only send the options actually provided -------------

def test_task_edit_partial_options(home):
    runner.invoke(app, ["--json", "projects", "create", "P", "--set-active"])
    r = runner.invoke(app, ["--json", "task", "add", "Fix dash", "--tag", "gameplay"])
    assert r.exit_code == 0, r.output
    tid = json.loads(r.output)["id"]
    # only --priority: title/tags must be left untouched (no TypeError/IntegrityError)
    r = runner.invoke(app, ["--json", "task", "edit", str(tid), "--priority", "high"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["priority"] == "high"
    assert data["title"] == "Fix dash" and data["tags"] == ["gameplay"]


def test_guard_converts_domain_errors(home):
    import sqlite3
    with pytest.raises(Exception):
        deps.guard(lambda: (_ for _ in ()).throw(GitError("broken repo")))
    with pytest.raises(Exception):
        deps.guard(lambda: (_ for _ in ()).throw(sqlite3.IntegrityError("dup")))
    with pytest.raises(Exception):
        deps.guard(lambda: (_ for _ in ()).throw(TypeError("bad type")))
    # and they surface as clean CLI errors (exit code 2, no traceback)
    runner.invoke(app, ["--json", "projects", "create", "P", "--set-active"])
    r = runner.invoke(app, ["task", "edit", "999", "--priority", "high"])
    assert r.exit_code != 0 and "Traceback" not in r.output


# --- CLI: projects delete / log edit / log delete ----------------------------

def test_cli_projects_delete_and_log_edit_delete(home):
    runner.invoke(app, ["--json", "projects", "create", "Doomed", "--set-active"])
    r = runner.invoke(app, ["--json", "log", "add", "une note"])
    assert r.exit_code == 0, r.output
    lid = json.loads(r.output)["id"]
    r = runner.invoke(app, ["--json", "log", "edit", str(lid), "note corrigée"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["--json", "log", "delete", str(lid), "--yes"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["--json", "projects", "delete", "doomed", "--yes"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["--json", "projects", "list"])
    assert json.loads(r.output)["projects"] == []


# --- core: stats local-day + session auto-stop event ------------------------

def test_tasks_done_today_uses_local_day(home):
    with conn(home) as c:
        p = projects.create(c, "P")
        projects.set_active(c, p.id)
        t = tasks.add(c, p.id, "today task")
        tasks.set_status(c, t.id, "done")  # completed_at = now (UTC) = today local
        assert stats.tasks_done_today(c, p.id) == 1


def test_session_autostop_emits_stopped_event(home):
    with conn(home) as c:
        p = projects.create(c, "P")
        s1 = sessions.start(c, p.id)
        ev_before = [e for e in events.since(c, 0) if e.action == "stopped"]
        s2 = sessions.start(c, p.id)  # auto-stops s1
        ev_after = [e for e in events.since(c, 0) if e.action == "stopped"]
        assert len(ev_after) == len(ev_before) + 1
        assert int(ev_after[-1].entity_id) == s1.id
        assert sessions.current(c).id == s2.id


def test_tasks_update_rejects_none_on_not_null(home):
    with conn(home) as c:
        p = projects.create(c, "P")
        t = tasks.add(c, p.id, "x")
        with pytest.raises(ValueError):
            tasks.update(c, t.id, title=None)
        with pytest.raises(ValueError):
            tasks.update(c, t.id, priority=None)


def test_list_tags_sql(home):
    with conn(home) as c:
        p = projects.create(c, "P")
        tasks.add(c, p.id, "a", tags=["gameplay", "ui"])
        tasks.add(c, p.id, "b", tags=["audio"])
        assert tasks.list_tags(c, p.id) == ["audio", "gameplay", "ui"]


# --- security: backup zip permissions ---------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="chmod 0600 is POSIX-only (best-effort on Windows)")
def test_backup_zip_is_0600(home):
    from devdash.core.backup import make_backup
    runner.invoke(app, ["--json", "projects", "create", "P", "--set-active"])
    out = home / "bk"
    r = make_backup(home=home, out_dir=out)
    mode = os.stat(r["path"]).st_mode & 0o777
    assert mode == 0o600, oct(mode)


# --- MCP: garbage lines must not kill the server -----------------------------

def test_mcp_survives_non_dict_lines(home):
    env = {**os.environ, "DEVDASH_HOME": str(home), "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "devdash", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(ROOT),
    )
    try:
        lines = ["[1,2]", '"hello"', "42", "null",
                 json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})]
        out, err = proc.communicate("\n".join(lines) + "\n", timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err  # server exited cleanly, not crashed
    responses = [json.loads(l) for l in out.strip().splitlines() if l.strip()]
    # 4 invalid-request errors + 1 pong; the ping MUST be answered after garbage
    assert len(responses) == 5
    assert responses[-1].get("result") == {}
    assert all(r.get("error") for r in responses[:4])


def test_mcp_task_add_validates_estimate_and_limit(home):
    from devdash.mcp_server import _call
    c = conn(home)
    p = projects.create(c, "P")
    projects.set_active(c, p.id)
    with pytest.raises(ValueError):
        _call("devdash_task_add", {"title": "x", "estimate_min": -5}, home)
    t = _call("devdash_task_add", {"title": "ok", "estimate_min": 30}, home)
    assert t["estimate_min"] == 30
    c.close()


# --- GUI api: limit clamp, out confinement, update confirm -------------------

def test_api_int_clamp_and_confined_out(home):
    from devdash.gui.api import Api, ApiError
    api = Api(home=home)
    assert Api._int("-1", "limit", 50, hi=Api.MAX_LIST_LIMIT) == 1
    assert Api._int("999999", "limit", 50, hi=Api.MAX_LIST_LIMIT) == Api.MAX_LIST_LIMIT
    with pytest.raises(ApiError) as ei:
        api._confined_out({"out": "C:/Windows/evil" if os.name == "nt" else "/etc/evil"}, "backups")
    assert ei.value.status == 400
    inside = api._confined_out({"out": str(home / "sub")}, "backups")
    assert str(inside).startswith(str(home))
    with pytest.raises(ApiError):
        api.update_now({})  # missing confirm -> 400


# --- task deletion (core + CLI + GUI wiring) ---------------------------------

def test_task_delete_cascades_subtasks(home):
    with conn(home) as c:
        p = projects.create(c, "P")
        t = tasks.add(c, p.id, "parent")
        sub = tasks.add(c, p.id, "child")
        tasks.update(c, sub.id, parent_id=t.id)
        tasks.delete(c, t.id)
        assert tasks.get(c, t.id) is None
        assert tasks.get(c, sub.id) is None  # ON DELETE CASCADE
        with pytest.raises(KeyError):
            tasks.delete(c, t.id)


def test_cli_task_delete(home):
    runner.invoke(app, ["--json", "projects", "create", "P", "--set-active"])
    tid = json.loads(runner.invoke(app, ["--json", "task", "add", "x"]).output)["id"]
    r = runner.invoke(app, ["--json", "task", "delete", str(tid), "--yes"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["deleted"] == tid
    r = runner.invoke(app, ["--json", "task", "list"])
    assert json.loads(r.output)["count"] == 0


def test_gui_task_delete_wiring():
    from devdash.gui.api import Api
    html = (ROOT / "devdash" / "gui" / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "devdash" / "gui" / "static" / "app.js").read_text(encoding="utf-8")
    srv = (ROOT / "devdash" / "gui" / "server.py").read_text(encoding="utf-8")
    assert 'id="btn-delete-task"' in html
    assert '$("#btn-delete-task")' in js and "/api/tasks/delete" in js
    assert '"/api/tasks/delete"' in srv and "def tasks_delete" in (
        ROOT / "devdash" / "gui" / "api.py").read_text(encoding="utf-8")
    assert Api._int("5", "id") == 5  # endpoint parses id through the same helper


# --- GUI static: the audit-2 regression guards --------------------------------

def test_render_features_is_async_and_title_span_exists():
    js = (ROOT / "devdash" / "gui" / "static" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "devdash" / "gui" / "static" / "index.html").read_text(encoding="utf-8")
    # an `await` inside a non-async function is a parse-time SyntaxError that
    # kills the WHOLE script — renderFeatures fetches its detail, so it must be async
    assert "async function renderFeatures(" in js
    assert 'id="task-modal-title-text"' in html
    assert '$("#task-modal-title-text")' in js
    # dialog roles for a11y
    for m in ["modal", "modal-edit", "modal-task", "modal-help", "palette"]:
        assert f'id="{m}"' in html and 'role="dialog"' in html
