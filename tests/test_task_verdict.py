"""AI task verdict (`ai verify` / /api/ai/task_verdict / MCP devdash_task_verify).

Safety contract tested here: the verdict is read-only by default; apply=True
marks done ONLY on a confident positive verdict; the GUI endpoint never applies.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import config as cfgmod
from devdash.core import db, projects, tasks
from devdash.core.ai import assistant
from devdash.core.ai.provider import AIDisabledError

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


def enable_mock_ai(home: Path, **overrides) -> None:
    data = {"ai_enabled": True, "ai_provider": "mock", "ai_model": "mock"}
    data.update(overrides)
    (home / "config.json").write_text(json.dumps(data), encoding="utf-8")


def test_task_verdict_readonly_by_default(conn, home, project):
    enable_mock_ai(home)
    t = tasks.add(conn, project.id, "Dash i-frames")
    r = assistant.task_verdict(conn, t.id, apply=False, home=home)
    assert r["verdict"]["seems_complete"] is True      # mock says complete
    assert 0.0 <= r["verdict"]["confidence"] <= 1.0
    assert r["applied"] is False
    assert tasks.get(conn, t.id).status == "todo"        # NEVER auto-done


def test_task_verdict_apply_marks_done_only_with_consent(conn, home, project):
    enable_mock_ai(home)
    t = tasks.add(conn, project.id, "Dash")
    r = assistant.task_verdict(conn, t.id, apply=True, home=home)  # mock conf 0.8 >= 0.7
    assert r["applied"] is True
    assert tasks.get(conn, t.id).status == "done"


def test_task_verdict_apply_respects_low_confidence(conn, home, project, monkeypatch):
    enable_mock_ai(home)
    t = tasks.add(conn, project.id, "Dash")
    # force a weak verdict through the mock
    monkeypatch.setattr(assistant, "VERDICT_APPLY_MIN_CONFIDENCE", 0.99)
    r = assistant.task_verdict(conn, t.id, apply=True, home=home)
    assert r["applied"] is False and tasks.get(conn, t.id).status == "todo"


def test_task_verdict_flag_can_disable(conn, home, project):
    enable_mock_ai(home, ai_feature_flags={"task_verdict": False})
    t = tasks.add(conn, project.id, "x")
    with pytest.raises(AIDisabledError):
        assistant.task_verdict(conn, t.id, home=home)


def test_task_verdict_unknown_task(conn, home, project):
    enable_mock_ai(home)
    with pytest.raises(KeyError):
        assistant.task_verdict(conn, 999, home=home)


def test_cli_ai_verify(conn, home, project):
    enable_mock_ai(home)
    t = tasks.add(conn, project.id, "Boss fight")
    r = runner.invoke(app, ["--json", "ai", "verify", str(t.id)])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["applied"] is False and data["verdict"]["seems_complete"] is True
    # --apply is the explicit consent that marks done
    r = runner.invoke(app, ["--json", "ai", "verify", str(t.id), "--apply"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["applied"] is True


def test_api_task_verdict_never_applies(home):
    from devdash.gui.api import Api
    enable_mock_ai(home)
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    p = projects.create(c, "P")
    projects.set_active(c, p.id)
    t = tasks.add(c, p.id, "x")
    c.close()
    api = Api(home=home)
    r = api.ai_task_verdict({"id": t.id})  # even if a client sent apply=true, it is ignored
    assert r["applied"] is False
    c = db.connect(home / "devdash.db")
    assert tasks.get(c, t.id).status == "todo"
    c.close()


def test_mcp_task_verify_tool(home):
    from devdash.mcp_server import _call
    enable_mock_ai(home)
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    p = projects.create(c, "P")
    projects.set_active(c, p.id)
    t = tasks.add(c, p.id, "x")
    r = _call("devdash_task_verify", {"id": t.id}, home)
    assert r["verdict"]["seems_complete"] is True and r["applied"] is False
    assert tasks.get(c, t.id).status == "todo"
    c.close()


def test_gui_verify_wiring():
    root = Path(__file__).resolve().parent.parent
    html = (root / "devdash/gui/static/index.html").read_text(encoding="utf-8")
    js = (root / "devdash/gui/static/app.js").read_text(encoding="utf-8")
    for el in ["task-verdict-box", "btn-task-verify", "task-verdict-result", "btn-task-verify-approve", "flag-task-verdict"]:
        assert f'id="{el}"' in html, el
    for needle in ['$("#btn-task-verify")', "/api/ai/task_verdict", 'api.post("/api/tasks/status", { id, status: "done" })']:
        assert needle in js, needle
