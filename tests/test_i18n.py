# tests -- i18n
# Works on Windows, Mac and Linux.

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import config as cfgmod
from devdash.core import i18n
from devdash.gui.server import make_server

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def _set_lang(home: Path, lang: str) -> None:
    cfg = cfgmod.load_config(home)
    cfg.lang = lang
    cfgmod.save_config(cfg, home)


def test_default_lang_is_fr(home):
    assert i18n.get_lang(home) == "fr"
    assert i18n.tr("task.added", lang="fr", id=1, slug="game", title="Dash") == \
        "[green]✔[/] tâche [bold]#1[/] ajoutée à [bold]game[/]: Dash"


def test_english_translation(home):
    _set_lang(home, "en")
    assert i18n.get_lang(home) == "en"
    assert i18n.tr("task.added", id=1, slug="game", title="Dash") == \
        "[green]✔[/] task [bold]#1[/] added to [bold]game[/]: Dash"
    # unknown key falls back to the key itself; catalogs stay in sync
    assert i18n.tr("does.not.exist", lang="en") == "does.not.exist"
    assert set(i18n.FR) == set(i18n.EN), "fr/en catalogs must have identical keys"


def test_lang_change_picked_up_without_restart(home):
    assert i18n.tr("gui.add") == "Ajouter"
    _set_lang(home, "en")
    assert i18n.tr("gui.add") == "Add"  # mtime-keyed cache refreshes


def test_cli_french_then_english(home):
    r = runner.invoke(app, ["projects", "create", "Aether Vale", "--set-active"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["projects", "set-active", "aether-vale"])
    assert "projet actif est désormais" in r.output  # fr by default

    _set_lang(home, "en")
    r = runner.invoke(app, ["projects", "set-active", "aether-vale"])
    assert r.exit_code == 0, r.output
    assert "active project is now" in r.output
    assert "projet actif" not in r.output

    r = runner.invoke(app, ["task", "add", "Dash du joueur", "--priority", "high"])
    assert "task #1 added to" in r.output


def test_gui_i18n_endpoint(home):
    _set_lang(home, "en")
    server = make_server(port=0, home=home)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/i18n") as r:
            d = json.loads(r.read().decode("utf-8"))
        assert d["lang"] == "en"
        assert d["messages"]["gui.add"] == "Add"
        assert d["messages"]["task.added"].startswith("[green]")
    finally:
        server.shutdown()
        server.server_close()
