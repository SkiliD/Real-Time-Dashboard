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
# the REST layer is split into gui/api*.py mixins — concatenate them all
API_PY = "\n".join(f.read_text(encoding="utf-8")
                   for f in sorted((ROOT / "devdash/gui").glob("api*.py")))


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


def test_appjs_dnd_is_fluid():
    # custom drag ghost, move cursor, edge auto-scroll, drop feedback animations
    assert "setDragImage" in APP_JS and "drag-ghost" in APP_JS
    assert 'dropEffect = "move"' in APP_JS
    assert "scrollBy" in APP_JS
    assert "drop-flash" in APP_JS and "landed" in APP_JS


def test_css_dnd_animations():
    css = (ROOT / "devdash/gui/static/styles.css").read_text(encoding="utf-8")
    for needle in ["@keyframes card-land", "@keyframes col-flash", ".drag-ghost",
                   ".col.drop-hover", "color-mix", "prefers-reduced-motion"]:
        assert needle in css, needle
    assert ".card:hover" in css and "translateY(-2px)" in css


# --- long descriptions (long-term projects) --------------------------------------

def test_core_text_caps(conn):
    from devdash.core.models import MAX_TEXT_LEN, MAX_TITLE_LEN
    p = projects.create(conn, "demo")
    long_ok = "x" * 50_000
    t = tsk.add(conn, p.id, "task", description=long_ok)
    assert len(t.description) == 50_000
    with pytest.raises(ValueError):
        tsk.add(conn, p.id, "task2", description="x" * (MAX_TEXT_LEN + 1))
    with pytest.raises(ValueError):
        tsk.add(conn, p.id, "t" * (MAX_TITLE_LEN + 1))
    from devdash.core import logs
    e = logs.add(conn, "y" * 50_000, project_id=p.id)
    assert len(e.content) == 50_000
    with pytest.raises(ValueError):
        logs.add(conn, "y" * (MAX_TEXT_LEN + 1), project_id=p.id)


def test_cli_desc_file(home, tmp_path):
    import json
    from typer.testing import CliRunner
    from devdash.cli.main import app
    runner = CliRunner()
    note = tmp_path / "note.md"
    note.write_text("# spec\n" + "lorem ipsum\n" * 500, encoding="utf-8")  # ~6k chars, multiline
    r = runner.invoke(app, ["--json", "projects", "create", "LongProj", "--set-active"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["--json", "task", "add", "Big task", "--desc-file", str(note)])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["description"].startswith("# spec\n")
    assert len(data["description"]) > 5000
    # edit path too
    r = runner.invoke(app, ["--json", "task", "edit", str(data["id"]), "--desc-file", str(note)])
    assert r.exit_code == 0, r.output
    # log content from file
    r = runner.invoke(app, ["--json", "log", "add", "--content-file", str(note)])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["content"].startswith("# spec")


def test_gui_descriptions_are_textareas():
    for el in ("t-desc", "e-desc", "ft-desc", "log-content"):
        assert f'<textarea id="{el}"' in INDEX_HTML, el
        assert f'<input id="{el}"' not in INDEX_HTML, el
    assert "desc-input" in APP_JS and "scrollHeight" in APP_JS  # auto-grow
    assert "ctrlKey" in APP_JS and "addLog" in APP_JS  # Ctrl+Enter sends the devlog entry


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
