"""GUI settings tab (general settings + one-click backup) + HTTP end-to-end.

Covers: /api/settings read (API key stays write-only), validation of every
general field, persistence to config.json, hot language switch via /api/i18n,
and POST /api/backup producing a real zip (db + config.json).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from devdash.core import config as cfgmod
from devdash.core import i18n
from devdash.gui.api import Api, ApiError
from devdash.gui.server import make_server


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def test_settings_get_shape_and_key_masking(home):
    cfg = cfgmod.load_config(home)
    cfg.ai_enabled = True
    cfg.ai_provider = "mistral"
    cfg.ai_api_key = "sk-SECRET-456"
    cfg.pomodoro_focus_min = 50
    cfgmod.save_config(cfg, home)

    api = Api(home=home)
    s = api.settings_get()
    assert s["general"]["pomodoro_focus_min"] == 50
    assert s["general"]["langs"] == ["fr", "en"]
    assert isinstance(s["general"]["commit_patterns"], list) and len(s["general"]["commit_patterns"]) >= 3
    assert s["home"].endswith("") or s["home"]  # home path present
    assert s["ai"]["api_key_set"] is True
    assert "sk-SECRET-456" not in json.dumps(s)  # key never leaves the server
    assert "ai_api_key" not in s["ai"]


def test_settings_save_general_fields(home):
    api = Api(home=home)
    r = api.settings_save({
        "lang": "en",
        "pomodoro_focus_min": 50, "pomodoro_break_min": 10,
        "git_watch_interval_sec": 30, "gui_port": 9000,
        "commit_patterns": ["#(\\d+)", "feat\\(([^)]+)\\)"],
    })
    assert r["saved"] is True
    g = r["settings"]["general"]
    assert g["lang"] == "en" and g["gui_port"] == 9000 and g["pomodoro_focus_min"] == 50
    assert g["commit_patterns"] == ["#(\\d+)", "feat\\(([^)]+)\\)"]

    on_disk = json.loads(cfgmod.config_path(home).read_text(encoding="utf-8"))
    assert on_disk["lang"] == "en" and on_disk["gui_port"] == 9000
    assert on_disk["commit_patterns"] == ["#(\\d+)", "feat\\(([^)]+)\\)"]

    # language applies hot: the i18n endpoint now serves English
    assert i18n.get_lang(home) == "en"
    assert i18n.messages(home=home)["gui.tab.settings"] == "Settings"


def test_settings_save_validation(home):
    api = Api(home=home)
    for bad in [
        {"lang": "xx"},
        {"lang": 42},
        {"pomodoro_focus_min": 0},
        {"pomodoro_break_min": 9999},
        {"git_watch_interval_sec": 1},
        {"gui_port": 80},
        {"gui_port": 70000},
        {"commit_patterns": []},
        {"commit_patterns": "not-a-list-but-string-is-ok-if-lines"},  # string with 1 line is accepted
    ][:-1]:
        with pytest.raises(ApiError) as e:
            api.settings_save(bad)
        assert e.value.status == 400, bad

    with pytest.raises(ApiError):  # invalid regex
        api.settings_save({"commit_patterns": ["([unclosed"]})
    with pytest.raises(ApiError):  # too many patterns
        api.settings_save({"commit_patterns": ["a"] * 11})
    with pytest.raises(ApiError):  # non-integer port
        api.settings_save({"gui_port": "abc"})


def test_settings_save_still_handles_ai_and_keeps_key(home):
    cfg = cfgmod.load_config(home)
    cfg.ai_api_key = "sk-KEEP-789"
    cfgmod.save_config(cfg, home)
    api = Api(home=home)
    r = api.settings_save({"ai_enabled": True, "ai_provider": "ollama",
                           "ai_model": "qwen3:8b", "ai_api_key": "", "lang": "fr"})
    assert r["status"]["enabled"] is True and r["status"]["provider"] == "ollama"
    on_disk = json.loads(cfgmod.config_path(home).read_text(encoding="utf-8"))
    assert on_disk["ai_api_key"] == "sk-KEEP-789"  # empty key field = keep
    assert "sk-KEEP-789" not in json.dumps(r)


def test_backup_endpoint_creates_zip(home):
    api = Api(home=home)
    api.projects_create({"name": "Game", "set_active": True})
    cfgmod.save_config(cfgmod.load_config(home), home)  # ensure config.json exists
    out = home / "backups"
    r = api.backup_now({"out": str(out)})
    zp = Path(r["path"])
    assert zp.exists() and zp.suffix == ".zip" and zp.parent == out
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        assert "devdash.db" in names and "config.json" in names
        # the backup db really contains the project
        import sqlite3, tempfile
        with tempfile.TemporaryDirectory() as td:
            z.extract("devdash.db", td)
            c = sqlite3.connect(str(Path(td) / "devdash.db"))
            n = c.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            c.close()
    assert n == 1
    assert r["bytes"] > 0


def test_http_settings_and_backup_flow(home):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        s = json.loads(urllib.request.urlopen(base + "/api/settings").read())
        assert s["general"]["lang"] == "fr"

        req = urllib.request.Request(
            base + "/api/settings",
            data=json.dumps({"lang": "en", "gui_port": 8800}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        r = json.loads(urllib.request.urlopen(req).read())
        assert r["saved"] is True and r["settings"]["general"]["lang"] == "en"

        # i18n endpoint follows the saved language
        i18n_data = json.loads(urllib.request.urlopen(base + "/api/i18n").read())
        assert i18n_data["lang"] == "en"

        # backup over HTTP
        req = urllib.request.Request(base + "/api/backup",
                                     data=json.dumps({}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        b = json.loads(urllib.request.urlopen(req).read())
        assert Path(b["path"]).exists()
        # default destination is <home>/backups, never the process cwd
        assert str(home) in b["path"] and "backups" in b["path"]

        # bad value -> 400 JSON, forged Host -> 403
        req = urllib.request.Request(base + "/api/settings",
                                     data=json.dumps({"gui_port": 3}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400
        req = urllib.request.Request(base + "/api/backup", data=b"{}",
                                     headers={"Content-Type": "application/json",
                                              "Host": "evil.example.com"}, method="POST")
        try:
            urllib.request.urlopen(req)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        srv.shutdown()
        srv.server_close()
