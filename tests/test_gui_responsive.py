# tests -- responsive GUI: the app must adapt to the window size / screen
# resolution (sidebar -> icon rail, kanban stacks vertically, charts 1-col,
# no page-level horizontal scroll). Static checks on the shipped assets;
# real layout was verified headless in Edge at 1440/1000/900/700/500 px.

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "devdash/gui/static/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "devdash/gui/static/styles.css").read_text(encoding="utf-8")
JS = (ROOT / "devdash/gui/static/app.js").read_text(encoding="utf-8")


def _media_block(css: str, max_width: int) -> str:
    """Extract the body of the `@media (max-width: Npx)` block."""
    m = re.search(rf"@media \(max-width: {max_width}px\) \{{(.*?)\n\}}", css, re.S)
    assert m, f"missing @media (max-width: {max_width}px) block"
    return m.group(1)


def test_viewport_meta_present():
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in HTML


def test_app_height_uses_dvh_fallback():
    # 100dvh so mobile browser toolbars don't crop the app
    assert "height: 100dvh" in CSS


def test_breakpoints_exist_and_are_balanced():
    assert CSS.count("@media") >= 3
    stripped = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    assert stripped.count("{") == stripped.count("}"), "unbalanced CSS braces"


def test_narrow_sidebar_becomes_icon_rail():
    block = _media_block(CSS, 920)
    assert "#sidebar { width: 54px" in block
    assert "#btn-sidebar-toggle { display: none; }" in block
    # only the project color dot remains visible
    assert ".project-list li > *:not(.dot) { display: none; }" in block


def test_narrow_kanban_stacks_vertically_no_hscroll():
    block = _media_block(CSS, 920)
    assert "#kanban { flex-direction: column; overflow-y: auto; overflow-x: hidden; }" in block
    assert ".col { min-width: 0; }" in block


def test_charts_single_column_on_narrow():
    assert "grid-template-columns: 1fr;" in _media_block(CSS, 920)


def test_compact_breakpoint_shrinks_chrome():
    block = _media_block(CSS, 640)
    assert "#sidebar { width: 46px" in block
    assert ".settings-grid { grid-template-columns: 1fr; }" in block
    assert ".modal-card { width: 94vw" in block
    assert "#toast { left: 12px; right: 12px" in block


def test_header_and_session_panel_wrap():
    assert "flex-wrap: wrap" in CSS.split("#project-header {")[1].split("}")[0]
    assert "#session-panel { flex-wrap: wrap; }" in _media_block(CSS, 920)


def test_tabs_scroll_instead_of_crush():
    assert "#tabs { display: flex; gap: 4px; padding: 8px 20px 0; border-bottom: 1px solid var(--border); overflow-x: auto;" in CSS


# --- window-size / maximized adaptation (updateWinMode in app.js) -------------

def test_resize_handler_sets_wide_and_maximized_classes():
    assert "function updateWinMode()" in JS
    assert 'window.addEventListener("resize"' in JS  # fires on maximize/restore in browser AND pywebview
    assert 'classList.toggle("wide"' in JS
    assert 'classList.toggle("maximized"' in JS
    assert "screen.availWidth" in JS and "screen.availHeight" in JS  # maximized heuristic
    assert "updateWinMode();" in JS  # initial call at boot


def test_wide_and_maximized_css_rules():
    assert "body.wide #kanban" in CSS
    assert "body.wide .col { min-width: 300px; }" in CSS
    assert "body.wide .chart-box { height: 340px; }" in CSS
    assert "body.maximized:not(.sidebar-collapsed) #sidebar" in CSS  # must not fight the collapsed state
    assert "body.maximized #git-cmd-out { max-height: 55vh; }" in CSS


def test_short_window_media_query():
    m = re.search(r"@media \(max-height: 720px\) \{(.*?)\n\}", CSS, re.S)
    assert m, "missing @media (max-height: 720px) block"
    block = m.group(1)
    assert "#project-header { padding: 6px 14px;" in block
    assert ".chart-box { height: 210px; }" in block
    assert "#git-cmd-out { max-height: 32vh; }" in block
