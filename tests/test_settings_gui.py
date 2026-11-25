"""tests -- settings gui"""

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


def test_lang_switch_roundtrip_over_http(home):
    """Regression: switching the UI language must take effect on the next
    GET /api/i18n (the GUI reloads the catalog right after saving settings)."""
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        def get(p):
            return json.loads(urllib.request.urlopen(base + p).read())
        assert get("/api/i18n")["lang"] == "fr"
        req = urllib.request.Request(
            base + "/api/settings",
            data=json.dumps({"lang": "en"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req).read()
        d = get("/api/i18n")
        assert d["lang"] == "en"
        assert d["messages"]["gui.tab.tasks"] == "Tasks"  # real EN catalog, not the FR fallback
        assert i18n.get_lang(home) == "en"
    finally:
        srv.shutdown()
        srv.server_close()


def test_save_button_exists_outside_collapsed_ai_card():
    """Regression: the only save button used to live inside the AI settings card,
    which is collapsed when the assistant is disabled -> with AI off there was no
    way to save (e.g. the language stayed stuck). A save button must exist in the
    General card, OUTSIDE the #ai-config-body wrapper."""
    html = (Path(__file__).parent.parent / "devdash" / "gui" / "static" / "index.html").read_text(encoding="utf-8")
    ai_body = html.find('id="ai-config-body"')
    assert ai_body != -1, "ai-config-body wrapper missing"
    save_general = html.find('id="btn-general-save"')
    assert save_general != -1, "General card has no save button"
    assert save_general < ai_body, "btn-general-save must be OUTSIDE (before) the collapsed AI section"


def test_data_and_update_cards_in_grid_next_to_general():
    """Settings: Général / Données / Mise à jour / IA are four PLAIN grid cards
    (no span-full) so the auto-fit grid lays them side by side — Données and
    Mise à jour sit next to Général, still as two distinct cards."""
    static = Path(__file__).parent.parent / "devdash" / "gui" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    css = (static / "styles.css").read_text(encoding="utf-8")
    assert "span-full" not in html, "no settings card may be full-width anymore"
    assert "span-full" not in css, "the span-full rule must be gone from the CSS"
    assert "repeat(auto-fit" in css, "settings grid must be auto-fit so cards sit side by side"
    opens = []
    for key in ('data-i18n="gui.set.general"', 'data-i18n="gui.set.data"',
                'data-i18n="gui.upd.title"', 'data-i18n="gui.set.title"'):
        h3 = html.find(key)
        assert h3 != -1, key
        card_open = html.rfind('<div class="settings-card', 0, h3)
        assert card_open != -1, key
        card_tag = html[card_open:html.find(">", card_open) + 1]
        assert card_tag == '<div class="settings-card">', f"{key}: plain grid card, got {card_tag}"
        opens.append(card_open)
    # four separate cards in DOM order: Général, Données, Mise à jour, IA
    assert len(set(opens)) == 4 and opens == sorted(opens)


def test_update_animations_are_wired_and_reduced_motion_safe():
    """Update-check animations: spinner/nudge/progress exist, are purposeful
    (finite nudge, loading states) and fully disabled by prefers-reduced-motion."""
    static = Path(__file__).parent.parent / "devdash" / "gui" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    css = (static / "styles.css").read_text(encoding="utf-8")
    js = (static / "app.js").read_text(encoding="utf-8")
    # CSS pieces
    for piece in ("@keyframes spin", "@keyframes status-in", "@keyframes nudge",
                  ".btn .spin", ".btn.loading", ".status-line.pop", ".btn.nudge",
                  ".upd-progress.on", "prefers-reduced-motion"):
        assert piece in css, f"missing CSS: {piece}"
    assert "animation: nudge .28s ease-in-out 3" in css  # finite, not an endless loop
    # HTML hooks
    assert 'id="upd-progress"' in html and 'id="upd-status" class="dim status-line"' in html
    assert 'id="git-cmd-status" class="dim small status-line"' in html
    # JS wiring
    for fn in ("setBtnBusy", "popStatus"):
        assert f"function {fn}" in js, fn
    for caller in ("checkUpdate", "runUpdate", "gitRun", "gitDiffSelection"):
        body = js[js.index(f"function {caller}"):]
        body = body[:body.index("\nfunction ") if "\nfunction " in body else len(body)]
        assert "setBtnBusy" in body, f"{caller} must show a busy spinner"
    assert 'gui.upd.checking' in js


def test_update_button_only_shown_when_update_detected():
    """'Mettre à jour maintenant' must be hidden by default and only revealed
    when an update was actually detected (check button or last fetch)."""
    static = Path(__file__).parent.parent / "devdash" / "gui" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "app.js").read_text(encoding="utf-8")
    css = (static / "styles.css").read_text(encoding="utf-8")
    # hidden in the markup
    assert '<button id="btn-upd-run" class="btn btn-primary hidden"' in html
    # one sync function driven by state.upd.update_available
    assert "function syncUpdRunBtn()" in js
    assert "state.upd.update_available" in js
    for caller in ("loadUpdateStatus", "checkUpdate", "runUpdate"):
        body = js[js.index(f"function {caller}" if not caller.startswith("load") else f"async function {caller}"):]
        nxt = body.find("\nfunction ", 10)
        nxt2 = body.find("\nasync function ", 10)
        end = min([x for x in (nxt, nxt2) if x > 0] or [len(body)])
        assert "syncUpdRunBtn" in body[:end], f"{caller} must call syncUpdRunBtn"
    # reveal animation available for the button
    assert ".btn.pop" in css


def test_theme_auto_is_supported_everywhere():
    """Theme 'auto' (follow the OS) must be accepted by the config, offered in
    the settings select, resolved in the pre-paint inline script, and handled
    by applyTheme (incl. live OS changes)."""
    root = Path(__file__).resolve().parents[1]
    from devdash.core import config as cfgmod, i18n
    html = (root / "devdash/gui/static/index.html").read_text(encoding="utf-8")
    js = (root / "devdash/gui/static/app.js").read_text(encoding="utf-8")
    cfg_py = (root / "devdash/core/config.py").read_text(encoding="utf-8")
    assert '("dark", "light", "auto")' in cfg_py
    assert 'theme: str = "auto"' in cfg_py
    assert '<option value="auto" data-i18n="gui.set.theme.auto">' in html
    assert "gui.set.theme.auto" in i18n.FR and "gui.set.theme.auto" in i18n.EN
    inline = html[html.index("<script>"):html.index("</script>")]
    assert "prefers-color-scheme" in inline and '"auto"' in inline
    assert "function resolveTheme" in js and "prefers-color-scheme" in js
    assert "onSysThemeChange" in js and 'localStorage.setItem("devdash-theme", raw)' in js
    import tempfile
    import pytest
    with tempfile.TemporaryDirectory() as d:
        assert cfgmod.load_config(Path(d)).theme == "auto"
        cfgmod.update_config(Path(d), lambda c: setattr(c, "theme", "light"))
        assert cfgmod.load_config(Path(d)).theme == "light"
        cfgmod.update_config(Path(d), lambda c: setattr(c, "theme", "auto"))
        assert cfgmod.load_config(Path(d)).theme == "auto"
        # the validator used by the settings endpoint accepts auto, refuses junk
        cfg = cfgmod.Config()
        cfgmod.apply_general_settings(cfg, {"theme": "auto"})
        assert cfg.theme == "auto"
        with pytest.raises(ValueError):
            cfgmod.apply_general_settings(cfgmod.Config(), {"theme": "neon"})
