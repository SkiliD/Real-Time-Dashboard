"""Regression tests for the final global audit (core + GUI) fixes."""

from __future__ import annotations

from pathlib import Path

import pytest

from devdash.core import db, projects
from devdash.core import tasks as tsk
from devdash.gui.api import Api
from devdash.mcp_server import handle_request

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "devdash/gui/static/app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "devdash/gui/static/index.html").read_text(encoding="utf-8")
SERVER_PY = (ROOT / "devdash/gui/server.py").read_text(encoding="utf-8")
API_PY = (ROOT / "devdash/gui/api.py").read_text(encoding="utf-8")


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


def test_slugify_folds_accents(conn):
    p = projects.create(conn, "Jeu Été ☕")
    assert p.slug == "jeu-ete"


def test_sessions_minutes_aggregated(home, conn):
    p = projects.create(conn, "demo")
    projects.set_active(conn, p.id)
    t = tsk.add(conn, p.id, "task one")
    for tid, dur in ((t.id, 25), (t.id, 35), (None, 60)):
        conn.execute(
            "INSERT INTO sessions(project_id, task_id, kind, started_at, ended_at, duration_min) "
            "VALUES (?, ?, 'focus', '2026-01-01T10:00:00Z', '2026-01-01T11:00:00Z', ?)",
            (p.id, tid, dur),
        )
    conn.commit()
    out = Api(home=home).sessions_minutes({})
    assert out["minutes"] == {t.id: 60}  # aggregated SQL, no session rows shipped


def test_settings_general_block_deduped(home, conn):
    api = Api(home=home)
    g1 = api.settings_get()["general"]
    g2 = api.settings_save({})["settings"]["general"]
    assert list(g1) == list(g2)
    assert "lang" in g1 and "commit_patterns" in g1


def test_mcp_task_add_title_capped(home, conn):
    p = projects.create(conn, "demo")
    projects.set_active(conn, p.id)
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "devdash_task_add", "arguments": {"title": "x" * 200_000}}}
    resp = handle_request(req, home)
    assert resp["result"]["isError"] is True
    assert "too long" in resp["result"]["content"][0]["text"]


def test_appjs_sessions_panel_uses_recent_sessions():
    assert 'renderPanel("sessions", state.recentSessions' in APP_JS
    assert "state.sessAll" not in APP_JS and "loadAllSessions" not in APP_JS


def test_appjs_poll_uses_aggregated_minutes_and_single_session_source():
    assert '"/api/sessions/minutes"' in APP_JS
    assert '"/api/sessions/minutes"' in SERVER_PY and "sessions_minutes" in API_PY
    assert "loadSession(" not in APP_JS  # session now comes from /api/status


def test_appjs_modal_feat_in_shortcut_guards():
    assert '"#modal-feat"' in APP_JS.split("modalOpen =")[1].split("]")[0]
    assert "return closeFeatModal();" in APP_JS  # Escape closes the feature modal too


def test_help_tab_count_is_dynamic():
    assert 'id="help-tabs-max"' in INDEX_HTML
    assert "help-tabs-max" in APP_JS and "tabOrder().length" in APP_JS


def test_appjs_named_timer_constants():
    assert "ARM_MS" in APP_JS and "TOAST_MS" in APP_JS and "PALETTE_MAX" in APP_JS
    assert "}, 5000);" not in APP_JS and "}, 6000);" not in APP_JS


def test_folder_picker_linux_fallbacks(monkeypatch):
    """Linux CI runners have neither zenity nor kdialog: the command builder must
    degrade zenity -> kdialog -> None instead of crashing."""
    from devdash.gui import api as apimod
    monkeypatch.setattr(apimod.shutil, "which", lambda c: "/usr/bin/zenity" if c == "zenity" else None)
    assert apimod._folder_picker_command("t", "linux")[0] == "zenity"
    monkeypatch.setattr(apimod.shutil, "which", lambda c: "/usr/bin/kdialog" if c == "kdialog" else None)
    assert apimod._folder_picker_command("t", "linux")[0] == "kdialog"
    monkeypatch.setattr(apimod.shutil, "which", lambda c: None)
    assert apimod._folder_picker_command("t", "linux") is None
    assert apimod._folder_picker_command("t", "darwin")[0] == "osascript"


def test_cli_provider_forces_utf8_child(home, conn, tmp_path):
    """The CLI-AI subprocess must run with UTF-8 pipes, otherwise accents break
    on Windows runners whose console locale is cp1252 (seen in CI)."""
    import shlex
    import sys
    from devdash.core import config as cfgmod
    from devdash.core.ai import assistant
    p = projects.create(conn, "demo")
    projects.set_active(conn, p.id)
    script = tmp_path / "enc.py"
    script.write_text(
        "import sys, json\njson.load(sys.stdin)\nprint('ENC:' + sys.stdout.encoding.lower())\n",
        encoding="utf-8",
    )
    cfg = cfgmod.load_config(home)
    cfg.ai_enabled = True
    cfg.ai_provider = "cli"
    cfg.ai_cli_command = shlex.join([sys.executable, str(script)])
    cfgmod.save_config(cfg, home)
    r = assistant.ask(conn, "où en suis-je ?", home=home)
    assert "utf-8" in r["answer"]
