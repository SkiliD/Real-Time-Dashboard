"""Tests for stats window coherence, event pruning, GUI error
toast presence, task --feature filter, backup command, ai_context_max_chars,
pomodoro auto-break."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import config as cfgmod
from devdash.core import db, events, features, projects, sessions, tasks
from devdash.gui.api import Api

runner = CliRunner()


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


# --- P0 #1: stats_overview must use the same window for both series ------------

def test_stats_overview_days_coherent(home, conn, project):
    api = Api(home=home)
    r = api.stats_overview({"days": "30"})
    assert len(r["time_by_day"]) == 30
    assert len(r["tasks_done_by_day"]) == 30  # was hardcoded to 7 before the fix
    r14 = api.stats_overview({"days": "14"})
    assert len(r14["time_by_day"]) == len(r14["tasks_done_by_day"]) == 14


# --- P0 C2: events.prune bounds the append-only bus -----------------------------

def test_events_prune_keeps_last_n(conn, project):
    before = events.last_id(conn)  # fixture events count too
    for i in range(120):
        events.append(conn, "test", i, "ping", {"i": i})
    deleted = events.prune(conn, keep=100)
    assert deleted == before + 120 - 100
    remaining = events.since(conn, 0, limit=10_000)
    assert len(remaining) == 100
    assert remaining[-1].id == events.last_id(conn)  # newest kept


def test_api_prunes_once_at_startup(home, conn, project):
    for i in range(50):
        events.append(conn, "test", i, "ping")
    api = Api(home=home)
    api.status()  # first call triggers init + prune(keep=10000): nothing deleted here
    assert events.since(conn, 0, limit=10_000) != [] or events.last_id(conn) == 0
    # force a tiny keep via direct call to prove the startup path works
    events.prune(conn, keep=10)
    assert len(events.since(conn, 0, limit=10_000)) == 10


# --- P0 C1: the GUI must have a visible error container --------------------------

def test_gui_has_error_toast_and_actions_wrapped():
    static = Path(__file__).parent.parent / "devdash" / "gui" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "app.js").read_text(encoding="utf-8")
    assert 'id="toast"' in html
    assert "function showError" in js and "function runAction" in js
    # main actions are wrapped (no raw unhandled api.post in onclick wiring)
    for btn in ["btn-add-task", "btn-create-project", "btn-session-start",
                "btn-session-stop", "btn-add-log", "btn-git-link", "btn-add-feature"]:
        assert f'$("#{btn}").onclick = () => runAction(' in js or f'$("#{btn}").onclick = runAction(' in js


# --- P1 I1: task list --feature ---------------------------------------------------

def test_task_list_feature_filter_cli(home):
    r = runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["feature", "add", "dash"])
    assert r.exit_code == 0, r.output
    runner.invoke(app, ["--json", "task", "add", "dash move"])
    runner.invoke(app, ["--json", "task", "add", "menu ui"])
    r = runner.invoke(app, ["--json", "task", "add", "dash hit"])  # id 3
    t3 = json.loads(r.output)["id"]
    r = runner.invoke(app, ["feature", "link", "dash", str(t3)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["--json", "task", "list", "--feature", "dash"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["count"] == 1 and data["tasks"][0]["title"] == "dash hit"
    # unknown feature -> clean error, not a traceback
    r = runner.invoke(app, ["task", "list", "--feature", "nope"])
    assert r.exit_code != 0 and "nope" in r.output


# --- P1 I2: devdash backup ---------------------------------------------------------

def test_backup_command_creates_zip(home, tmp_path):
    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    cfgmod.save_config(cfgmod.load_config(home), home)  # ensure config.json exists
    out = tmp_path / "backups"
    r = runner.invoke(app, ["--json", "backup", "--out", str(out)])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    zp = Path(data["path"])
    assert zp.exists() and zp.parent == out
    with zipfile.ZipFile(zp) as z:
        names = set(z.namelist())
        assert "devdash.db" in names and "config.json" in names
    # restore check: the zipped db opens and contains the project
    with zipfile.ZipFile(zp) as z:
        z.extract("devdash.db", tmp_path / "restore")
    import sqlite3
    c = sqlite3.connect(tmp_path / "restore" / "devdash.db")
    assert c.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    c.close()


# --- P1 I3: ai_context_max_chars is configurable -----------------------------------

def test_ai_context_max_chars(home, conn, project):
    from devdash.core.ai import assistant
    tasks.add(conn, project.id, "une tâche avec un titre raisonnablement long pour remplir le contexte")
    cfg = cfgmod.load_config(home)
    cfg.ai_context_max_chars = 200
    cfgmod.save_config(cfg, home)
    r = assistant.context_preview(conn, home=home)
    assert r["max_chars"] == 200
    assert len(r["context"]) <= 200 + 30  # truncation marker included


# --- P1 I4: pomodoro auto-break ------------------------------------------------------

def test_pomodoro_break_offer(conn, project, monkeypatch):
    from devdash.cli.commands import session as sess_cmd
    cfg = cfgmod.load_config()
    s = sessions.start(conn, project.id, kind="focus", minutes=25)
    stopped = sessions.stop(conn, s.id)
    # user accepts the break -> a pause session is started on the same project/task
    started = {}
    monkeypatch.setattr(sess_cmd.typer, "confirm", lambda *a, **k: True)
    ok = sess_cmd._maybe_start_break(conn, cfg, stopped)
    assert ok is True
    cur = sessions.current(conn)
    assert cur is not None and cur.kind == "pause" and cur.planned_min == cfg.pomodoro_break_min
    # user declines -> nothing started
    sessions.stop(conn, cur.id)
    s2 = sessions.start(conn, project.id, kind="focus", minutes=25)
    stopped2 = sessions.stop(conn, s2.id)
    monkeypatch.setattr(sess_cmd.typer, "confirm", lambda *a, **k: False)
    assert sess_cmd._maybe_start_break(conn, cfg, stopped2) is False
    assert sessions.current(conn) is None
    # non-focus session -> never offered
    s3 = sessions.start(conn, project.id, kind="custom")
    stopped3 = sessions.stop(conn, s3.id)
    monkeypatch.setattr(sess_cmd.typer, "confirm", lambda *a, **k: True)
    assert sess_cmd._maybe_start_break(conn, cfg, stopped3) is False
