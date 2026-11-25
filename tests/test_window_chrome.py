# tests -- native window chrome: the pywebview window is frameless and the app
# ships its own title bar (#titlebar) with drag region + min/max/close buttons.
# Browser mode must be unaffected (title bar hidden unless html.native).

from __future__ import annotations

import re
from pathlib import Path

from devdash.core import i18n

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "devdash/gui/static/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "devdash/gui/static/styles.css").read_text(encoding="utf-8")
JS = (ROOT / "devdash/gui/static/app.js").read_text(encoding="utf-8")
DESKTOP = (ROOT / "devdash/gui/desktop.py").read_text(encoding="utf-8")

WIN_KEYS = ["gui.win.min_tt", "gui.win.max_tt", "gui.win.restore_tt", "gui.win.close_tt"]


def test_titlebar_markup_present():
    assert '<div id="titlebar">' in HTML
    for btn in ("btn-win-min", "btn-win-max", "btn-win-close"):
        assert f'id="{btn}"' in HTML, f"missing #{btn}"
    # every title-bar button tooltip goes through i18n
    titles = re.findall(r'id="btn-win-[a-z]+"[^>]*data-i18n-title="([^"]+)"', HTML)
    assert titles, "title-bar buttons must use data-i18n-title"
    for key in titles:
        assert key in i18n.FR and key in i18n.EN, f"missing i18n key {key}"


def test_native_class_only_when_pywebview():
    # head inline script: show the custom bar only inside the native window
    assert 'if (window.pywebview || /[?&]native=1/.test(location.search)) document.documentElement.classList.add("native")' in HTML


def test_titlebar_hidden_in_browser_mode():
    assert re.search(r"#titlebar\s*\{\s*display:\s*none", CSS)
    assert "html.native #titlebar" in CSS
    # the bar is the drag region, the buttons are not
    assert "-webkit-app-region: drag" in CSS
    assert "-webkit-app-region: no-drag" in CSS
    # app height shrinks by the bar height in native mode
    assert "html.native #app" in CSS
    assert "calc(100dvh - 34px)" in CSS
    # close button gets the destructive hover style
    assert ".tb-btn.tb-close:hover" in CSS


def test_desktop_creates_frameless_window_with_api():
    assert "frameless=True" in DESKTOP
    assert "easy_drag=False" in DESKTOP  # only the title bar drags, not the page
    assert "shadow=True" in DESKTOP       # keep the native drop shadow
    assert "background_color=bg" in DESKTOP  # no white flash on load
    assert "js_api=" in DESKTOP
    for method in ("def minimize(", "def toggle_maximize(", "def close("):
        assert method in DESKTOP, f"_WindowApi missing {method}"


def test_appjs_wires_window_controls():
    assert "function initWindowControls()" in JS
    assert 'window.addEventListener("pywebviewready", initWindowControls)' in JS
    assert "api.minimize()" in JS
    assert "api.toggle_maximize()" in JS
    assert "api.close()" in JS
    # double-click on the bar toggles maximize, but not when clicking a button
    assert "dblclick" in JS
    assert 'closest(".tb-btn")' in JS
    # in browser mode (no pywebview) the controls must stay inert
    assert "window.pywebview && window.pywebview.api" in JS


def test_win32_drag_fallback_present():
    # Verified on winforms+WebView2 (pywebview 6.2.1): -webkit-app-region is NOT
    # honored (WM_NCHITTEST over a drag region returns HTCLIENT) and a synthetic
    # WM_NCLBUTTONDOWN/HTCAPTION does not move the window (WebView2 keeps mouse
    # capture). So start_drag() must track the cursor itself (GetCursorPos +
    # GetAsyncKeyState + SetWindowPos) while the left button is held.
    assert "def start_drag(" in DESKTOP
    assert "GetCursorPos" in DESKTOP and "GetAsyncKeyState" in DESKTOP
    assert "SetWindowPos" in DESKTOP
    # pywebview 6.x exposes the winforms Form as window.native (no .hwnd attr)
    assert 'getattr(win, "native", None)' in DESKTOP
    assert "FindWindowW" in DESKTOP  # caption-based fallback to locate the hwnd
    assert 'bar.addEventListener("mousedown"' in JS
    assert "api.start_drag" in JS
    # CSS app-region stays as the native path on backends that honor it
    assert "app-region: drag" in CSS
    assert "app-region: no-drag" in CSS


def test_taskbar_icon_passed_to_webview_start():
    # winforms builds Form.Icon (taskbar / alt-tab) from webview.start(icon=...)
    assert "icon.ico" in DESKTOP
    assert "webview.start(icon=" in DESKTOP
    # without an explicit AppUserModelID the taskbar groups the window under
    # python.exe and shows the Python icon; ICON_SMALL must be set explicitly
    # (the taskbar reads the small icon, not Form.Icon)
    assert "SetCurrentProcessExplicitAppUserModelID" in DESKTOP
    assert "_apply_window_icon" in DESKTOP
    assert "0x0080" in DESKTOP  # WM_SETICON


def test_csp_pins_inline_theme_script_by_hash():
    # the CSP must pin the exact inline theme script (no 'unsafe-inline' for
    # scripts) — recompute the hash from index.html and compare
    import base64
    import hashlib

    from devdash.gui import server

    m = re.search(r"<script>(.*?)</script>", HTML, re.S)
    assert m, "inline theme script not found in index.html"
    digest = base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode()
    csp = dict(server.SECURITY_HEADERS)["Content-Security-Policy"]
    assert f"sha256-{digest}" in csp
    script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src


def test_win_i18n_keys_in_both_langs():
    for key in WIN_KEYS:
        assert key in i18n.FR, f"{key} missing in FR"
        assert key in i18n.EN, f"{key} missing in EN"
    assert set(i18n.FR) == set(i18n.EN), "fr/en catalogs must stay in sync"


# --- frameless window must stay RESIZABLE (WS_THICKFRAME re-added) -----------

def test_edge_resize_is_reenabled_on_frameless_window():
    assert "WS_THICKFRAME = 0x00040000" in DESKTOP
    assert "WS_CAPTION = 0x00C00000" in DESKTOP  # native (accent/blue) title bar stripped
    assert "def _enable_edge_resize(win)" in DESKTOP
    assert "_enable_edge_resize(win)" in DESKTOP  # wired in launch
    assert "SetWindowLongW(hwnd, GWL_STYLE" in DESKTOP
    assert "0x0020" in DESKTOP  # SWP_FRAMECHANGED so the new style applies
    assert "DwmSetWindowAttribute(hwnd, 35" in DESKTOP  # DWMWA_CAPTION_COLOR = none
    assert "DwmSetWindowAttribute(hwnd, 34" in DESKTOP  # DWMWA_BORDER_COLOR = none
    assert "DwmSetWindowAttribute(hwnd, 2," in DESKTOP  # DWMWA_NCRENDERING_POLICY = DISABLED (no system caption/frame band from WS_THICKFRAME)
    assert "DwmSetWindowAttribute(hwnd, 33," in DESKTOP  # DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND (rounded window corners)
    assert "min_size=(720, 480)" in DESKTOP  # OS-enforced minimum size


def test_frameless_style_math():
    from devdash.gui import desktop
    f = desktop._frameless_style
    assert f(0) == 0x00040000                                  # thickframe in, nothing else
    assert f(0x80000000) == 0x80040000                         # keeps WS_POPUP
    assert f(0x00C00000 | 0x00800000 | 0x00400000) == 0x00040000  # caption+border+dlgframe stripped
    assert f(0x80C00000) == 0x80040000                         # popup+caption -> popup+thickframe
    assert f(f(0x80CF0000)) == f(0x80CF0000)                   # idempotent


# --- window geometry persisted at close, restored at launch --------------------

def test_window_geom_saved_on_close_and_restored_on_launch():
    assert "def _save_window_geom(win, home" in DESKTOP
    assert "def _restore_window_state(win, geom" in DESKTOP
    assert "win = webview.create_window(" in DESKTOP  # keep the returned Window object…
    assert "win.events.closed += lambda: _save_window_geom(win, home)" in DESKTOP  # hook at close (webview.windows is EMPTY then)
    assert 'width=geom["w"] if geom else 1360' in DESKTOP           # restore size
    assert '**({"x": geom["x"], "y": geom["y"]} if geom else {})' in DESKTOP  # restore position
    assert '_clamp_window_geom(cfg.gui_window or {})' in DESKTOP    # read persisted geom
    assert '"gui_window": (dict,)' in (ROOT / "devdash/core/config.py").read_text(encoding="utf-8")


def test_clamp_window_geom_pure():
    from devdash.gui import desktop
    assert desktop._clamp_window_geom({"w": 1360, "h": 860, "x": 40, "y": 30, "maximized": True}) == \
        {"w": 1360, "h": 860, "x": 40, "y": 30, "maximized": True}
    assert desktop._clamp_window_geom({"w": 1360, "h": 860}) == \
        {"w": 1360, "h": 860, "x": 0, "y": 0, "maximized": False}  # missing x/y default to 0
    assert desktop._clamp_window_geom({"w": 1360, "h": 860, "x": -5000, "y": 99999})["x"] == -64
    assert desktop._clamp_window_geom({"w": 1360, "h": 860, "x": -5000, "y": 99999})["y"] == 4096
    for bad in (None, {}, {"w": 10, "h": 860}, {"w": 99999, "h": 860}, {"w": "x", "h": 860}, {"w": 1360}):
        assert desktop._clamp_window_geom(bad) is None, bad


def test_config_roundtrip_gui_window(tmp_path):
    import json
    from devdash.core import config
    cfg = config.Config()
    assert cfg.gui_window == {}
    (tmp_path / "config.json").write_text(json.dumps({"gui_window": {"w": 1000, "h": 700, "x": 5, "y": 5, "maximized": False}}), encoding="utf-8")
    loaded = config.load_config(tmp_path)
    assert loaded.gui_window == {"w": 1000, "h": 700, "x": 5, "y": 5, "maximized": False}
    loaded.gui_window = {"w": 1200, "h": 800, "x": 10, "y": 10, "maximized": True}
    config.save_config(loaded, tmp_path)
    assert config.load_config(tmp_path).gui_window["w"] == 1200
    # wrong-typed value in a hand-edited file falls back to the default, no crash
    (tmp_path / "config.json").write_text(json.dumps({"gui_window": "oops"}), encoding="utf-8")
    assert config.load_config(tmp_path).gui_window == {}


def test_ui_radius_softened():
    assert "--radius: 10px;" in CSS  # cards/panels slightly rounder (window corners via DWM attr 33)


def test_window_geom_clamped_to_real_screen():
    from devdash.gui import desktop
    g = {"w": 1360, "h": 860, "x": 3000, "y": 5000, "maximized": False}
    work = (0, 0, 1920, 1040)  # typical 1080p work area
    out = desktop._clamp_to_workarea(g, work)
    assert out["x"] == 1920 - 64          # at least 64 px visible on the right edge
    assert out["y"] == 1040 - 64
    g2 = {"w": 1360, "h": 860, "x": -5000, "y": -100, "maximized": False}
    out2 = desktop._clamp_to_workarea(g2, work)
    assert out2["x"] == 0 - 1360 + 64     # at least 64 px visible on the left edge
    assert out2["y"] == 0
    assert "_clamp_to_workarea(geom, wa)" in DESKTOP and "_primary_workarea()" in DESKTOP
