"""CLI tests (step b): projects + tasks commands, human and --json output.

Uses typer's CliRunner with an isolated DEVDASH_HOME per test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def test_projects_create_list_set_active_json(home):
    r = runner.invoke(app, ["--json", "projects", "create", "Aether Vale", "--engine", "unity",
                            "--color", "#7c5cff", "--set-active"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["slug"] == "aether-vale" and data["engine"] == "unity"

    r = runner.invoke(app, ["--json", "projects", "list"])
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data["active"] == "aether-vale"
    assert len(data["projects"]) == 1 and data["projects"][0]["name"] == "Aether Vale"


def test_switch_active_keeps_data_json(home):
    runner.invoke(app, ["--json", "projects", "create", "Game One", "--set-active"])
    runner.invoke(app, ["--json", "projects", "create", "Site Two"])
    r = runner.invoke(app, ["--json", "task", "add", "Boss fight", "--tag", "gameplay"])
    assert r.exit_code == 0
    runner.invoke(app, ["--json", "projects", "set-active", "site-two"])
    r = runner.invoke(app, ["--json", "projects", "list"])
    data = json.loads(r.output)
    assert data["active"] == "site-two" and len(data["projects"]) == 2
    # task of project 1 still there
    r = runner.invoke(app, ["--json", "task", "list", "--project", "game-one"])
    assert json.loads(r.output)["count"] == 1


def test_task_flow_json(home):
    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    r = runner.invoke(app, ["--json", "task", "add", "Dash", "--priority", "high",
                            "--tag", "gameplay", "--tag", "combat", "--est", "90"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["id"] == 1 and data["status"] == "todo"
    assert data["tags"] == ["gameplay", "combat"] and data["estimate_min"] == 90
    assert data["project"] == "game"

    r = runner.invoke(app, ["--json", "task", "start", "1"])
    assert json.loads(r.output)["status"] == "in_progress"

    r = runner.invoke(app, ["--json", "task", "list", "--status", "in_progress"])
    assert json.loads(r.output)["count"] == 1

    r = runner.invoke(app, ["--json", "task", "done", "1"])
    data = json.loads(r.output)
    assert data["status"] == "done" and data["completed_at"] is not None

    r = runner.invoke(app, ["--json", "task", "list"])
    assert json.loads(r.output)["count"] == 1  # done tasks still listed by default


def test_task_add_without_active_project_fails_cleanly(home):
    r = runner.invoke(app, ["task", "add", "X"])
    assert r.exit_code != 0
    assert "aucun projet actif" in r.output


def test_session_flow_json(home):
    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    runner.invoke(app, ["task", "add", "Dash"])

    r = runner.invoke(app, ["--json", "session", "start", "--background", "--task", "1", "--minutes", "25"])
    assert r.exit_code == 0, r.output
    s = json.loads(r.output)
    assert s["ended_at"] is None and s["planned_min"] == 25 and s["task_id"] == 1

    r = runner.invoke(app, ["--json", "session", "now"])
    cur = json.loads(r.output)
    assert cur["running"] is True and cur["session"]["id"] == s["id"]

    # starting again auto-stops the previous one
    r = runner.invoke(app, ["--json", "session", "start", "--background"])
    s2 = json.loads(r.output)
    assert s2["id"] != s["id"]

    r = runner.invoke(app, ["--json", "session", "stop", "--note", "fait le dash"])
    assert r.exit_code == 0, r.output
    done = json.loads(r.output)
    assert done["note"] == "fait le dash" and done["duration_min"] is not None

    r = runner.invoke(app, ["--json", "session", "now"])
    assert json.loads(r.output)["running"] is False

    r = runner.invoke(app, ["--json", "session", "list"])
    data = json.loads(r.output)
    assert data["count"] == 2  # s (auto-stopped) + s2


def test_interactive_timer_exits_when_no_session(home, monkeypatch):
    """The blocking timer loop must exit cleanly when no session runs."""
    from devdash.cli.commands import session as session_cmd
    from devdash.core import db, config
    from devdash.core import sessions as core_sessions

    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    runner.invoke(app, ["--json", "session", "start", "--background"])

    calls = {"n": 0}
    real_current = core_sessions.current

    def fake_current(conn):
        calls["n"] += 1
        return real_current(conn) if calls["n"] < 3 else None

    monkeypatch.setattr(core_sessions, "current", fake_current)
    monkeypatch.setattr(session_cmd.time, "sleep", lambda s: None)
    conn = db.connect(config.db_path())
    db.init_db(conn)
    session_cmd._interactive_timer(conn, pomodoro=False)  # returns once current is None
    conn.close()
    assert calls["n"] >= 3


def test_interactive_timer_ctrl_c_stops_with_note(home, monkeypatch):
    """Ctrl+C in the timer -> prompt note -> session stopped."""
    from devdash.cli.commands import session as session_cmd
    from devdash.core import db, config
    from devdash.core import sessions as core_sessions

    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    runner.invoke(app, ["--json", "session", "start", "--background"])

    def boom(_s):
        raise KeyboardInterrupt()

    monkeypatch.setattr(session_cmd.time, "sleep", boom)
    monkeypatch.setattr(session_cmd.typer, "prompt", lambda *a, **k: "fait X")
    monkeypatch.setattr(session_cmd.typer, "confirm", lambda *a, **k: False)  # decline the auto-break
    conn = db.connect(config.db_path())
    db.init_db(conn)
    session_cmd._interactive_timer(conn, pomodoro=True)  # catches KeyboardInterrupt internally
    assert core_sessions.current(conn) is None
    hist = core_sessions.list(conn)
    assert hist[0].note == "fait X" and hist[0].ended_at is not None
    conn.close()


def test_log_flow_json(home):
    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    r = runner.invoke(app, ["--json", "log", "add", "Décision : dash = 0.3s i-frames",
                            "--tag", "decision", "--tag", "combat"])
    assert r.exit_code == 0, r.output
    e = json.loads(r.output)
    assert e["project"] == "game" and e["tags"] == ["decision", "combat"]

    runner.invoke(app, ["log", "add", "Bloqué sur le build WebGL", "--tag", "blocker"])
    runner.invoke(app, ["log", "add", "Idée : météo dynamique"])

    r = runner.invoke(app, ["--json", "log", "recent", "--limit", "10"])
    data = json.loads(r.output)
    assert data["count"] == 3
    assert data["entries"][0]["content"] == "Idée : météo dynamique"  # newest first

    r = runner.invoke(app, ["--json", "log", "search", "build"])
    data = json.loads(r.output)
    assert data["count"] == 1 and "WebGL" in data["entries"][0]["content"]


def test_git_flow_json(home, git_repo):
    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    r = runner.invoke(app, ["--json", "git", "link", str(git_repo)])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["project"] == "game"

    r = runner.invoke(app, ["--json", "git", "sync"])
    d = json.loads(r.output)
    assert d["repos"][0]["new"] == 3

    # idempotent
    r = runner.invoke(app, ["--json", "git", "sync"])
    assert json.loads(r.output)["repos"][0]["new"] == 0

    r = runner.invoke(app, ["--json", "git", "log", "--task", "2"])
    d = json.loads(r.output)
    assert d["count"] == 1 and d["commits"][0]["parsed_task_id"] == 2

    r = runner.invoke(app, ["--json", "git", "log", "--unparsed"])
    assert json.loads(r.output)["count"] == 1

    r = runner.invoke(app, ["--json", "git", "status"])
    st = json.loads(r.output)
    assert st["linked"] is True and st["commits_today"] == 3 and st["last_commit"]


def test_status_json(home):
    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    runner.invoke(app, ["task", "add", "Dash"])
    runner.invoke(app, ["--json", "session", "start", "--background"])
    r = runner.invoke(app, ["--json", "status"])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)
    assert d["project"]["slug"] == "game"
    assert d["tasks"]["open"] == 1
    assert d["session"]["running"] if "running" in d["session"] else d["session"] is not None
    assert d["session"]["ended_at"] is None
    assert "streak_days" in d and "today_minutes" in d and "git" in d and "progress" in d


def test_status_watch_loop_emits_then_exits(home, monkeypatch):
    """--watch loops and prints one JSON line per tick until Ctrl+C."""
    from devdash.cli.commands import status as status_cmd

    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    calls = {"n": 0}

    def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(status_cmd.time, "sleep", fake_sleep)
    r = runner.invoke(app, ["--json", "status", "--watch", "--interval", "0"])
    assert r.exit_code == 0, r.output
    lines = [ln for ln in r.output.strip().splitlines() if ln.strip()]
    assert len(lines) == 2  # two ticks before the simulated Ctrl+C
    assert json.loads(lines[0])["project"]["slug"] == "game"


def test_feature_flow_json(home):
    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    runner.invoke(app, ["task", "add", "Dash movement"])
    runner.invoke(app, ["task", "add", "Dash i-frames"])

    r = runner.invoke(app, ["--json", "feature", "add", "dash"])
    assert r.exit_code == 0, r.output
    fid = json.loads(r.output)["id"]

    runner.invoke(app, ["--json", "feature", "link", "dash", "1", "2"])
    r = runner.invoke(app, ["--json", "feature", "show", "dash"])
    d = json.loads(r.output)
    assert d["progress"]["total"] == 2 and d["completion_signal"]["all_done"] is False
    assert d["status"] == "open"  # never auto-done

    runner.invoke(app, ["task", "done", "1"])
    runner.invoke(app, ["task", "done", "2"])
    r = runner.invoke(app, ["--json", "feature", "show", "dash"])
    assert json.loads(r.output)["completion_signal"]["all_done"] is True
    assert json.loads(r.output)["status"] == "open"  # still open!

    r = runner.invoke(app, ["--json", "feature", "done", "dash", "--note", "testé OK"])
    d = json.loads(r.output)
    assert d["status"] == "done" and d["validated_at"] is not None

    # propose -> reject (feedback) -> approve
    runner.invoke(app, ["feature", "reopen", "dash"])
    r = runner.invoke(app, ["--json", "feature", "propose", "dash", "--confidence", "0.9", "--reasoning", "tasks done"])
    assert json.loads(r.output)["status"] == "proposed"
    r = runner.invoke(app, ["--json", "feature", "reject", "dash", "--feedback", "attendre tests"])
    assert json.loads(r.output)["status"] == "rejected"
    r = runner.invoke(app, ["--json", "feature", "approve", "dash"])
    assert json.loads(r.output)["status"] == "done"


def test_ai_commands_with_mock_provider(home):
    (home / "config.json").write_text(json.dumps(
        {"ai_enabled": True, "ai_provider": "mock", "ai_model": "mock"}), encoding="utf-8")
    runner.invoke(app, ["projects", "create", "Game", "--set-active"])

    r = runner.invoke(app, ["--json", "ai", "ask", "où en suis-je ?"])
    assert r.exit_code == 0, r.output
    assert "answer" in json.loads(r.output)

    r = runner.invoke(app, ["--json", "ai", "plan", "système de dash"])
    d = json.loads(r.output)
    assert d["applied"] is False and len(d["proposal"]) == 3  # no write without --apply

    r = runner.invoke(app, ["--json", "ai", "plan", "système de dash", "--apply"])
    d = json.loads(r.output)
    assert d["applied"] is True and len(d["created_task_ids"]) == 3

    r = runner.invoke(app, ["--json", "ai", "summarize", "--period", "week"])
    assert r.exit_code == 0 and "summary" in json.loads(r.output)


def test_ai_disabled_fails_cleanly(home):
    runner.invoke(app, ["projects", "create", "Game", "--set-active"])
    r = runner.invoke(app, ["ai", "ask", "test"])
    assert r.exit_code != 0
    assert "désactivée" in r.output


def test_human_output_is_pretty(home):
    runner.invoke(app, ["projects", "create", "Aether Vale", "--engine", "unity"])
    r = runner.invoke(app, ["projects", "list"])
    assert r.exit_code == 0
    assert "Aether Vale" in r.output and "aether-vale" in r.output and "unity" in r.output
    r = runner.invoke(app, ["projects", "set-active", "aether-vale"])
    assert "projet actif" in r.output
    runner.invoke(app, ["task", "add", "Dash du joueur", "--priority", "high"])
    r = runner.invoke(app, ["task", "list"])
    assert "Dash du joueur" in r.output and "high" in r.output
