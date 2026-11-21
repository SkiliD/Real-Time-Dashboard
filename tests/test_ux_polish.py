"""UX-polish consistency checks: the new palette/shortcuts/toasts layer must stay
wired to real DOM ids and real i18n keys (no node available here, so we check
statically)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "devdash" / "gui" / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "devdash" / "gui" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "devdash" / "gui" / "static" / "styles.css").read_text(encoding="utf-8")


def test_every_js_selector_id_exists_in_dom():
    # ids referenced from JS via $("#x") / getElementById("x")
    js_ids = set(re.findall(r'\$\("#([\w-]+)"\)', JS))
    js_ids |= set(re.findall(r'getElementById\("([\w-]+)"\)', JS))
    # ids present in static HTML or created dynamically in JS templates
    dom_ids = set(re.findall(r'id="([\w-]+)"', HTML)) | set(re.findall(r'id="([\w-]+)"', JS))
    missing = js_ids - dom_ids
    assert not missing, f"JS references ids missing from the DOM: {sorted(missing)}"


def test_new_ux_elements_present_in_html():
    for el in ["palette", "palette-input", "palette-list", "modal-help", "btn-close-help",
               "badge-tasks", "badge-features", "session-eta", "tasks-empty", "btn-copy-task-id"]:
        assert f'id="{el}"' in HTML, f"missing #{el} in index.html"


def test_new_ux_styles_present_in_css():
    for sel in [".tab-badge", "#palette", ".palette-item", ".palette-section", "#toast.ok",
                "kbd", ".help-table", "#tasks-empty", "h1.clickable", ":focus-visible",
                "prefers-reduced-motion"]:
        assert sel in CSS, f"missing CSS for {sel}"


def test_app_js_uses_new_ux_hooks():
    for needle in ["openPalette", "closePalette", "paletteKey", "timeAgo(", "notify(",
                   "saveUi(", "loadUi(", 'addEventListener("keydown"', "localStorage",
                   "btn-copy-task-id", "session-eta"]:
        assert needle in JS, f"app.js missing {needle}"


def test_all_i18n_keys_used_in_html_exist_in_both_langs():
    from devdash.core import i18n
    keys = set(re.findall(r'data-i18n(?:-ph|-title)?="([\w.]+)"', HTML))
    for lang in ("fr", "en"):
        msgs = i18n.messages(lang)
        missing = sorted(k for k in keys if k not in msgs)
        assert not missing, f"{lang} catalog missing keys used in HTML: {missing}"


def test_new_ux_keys_exist_and_parity_holds():
    from devdash.core import i18n
    fr, en = i18n.messages("fr"), i18n.messages("en")
    assert set(fr) == set(en), "FR/EN catalogs out of sync"
    for key in ["gui.palette.ph", "gui.palette.hint", "gui.palette.empty", "gui.help.title",
                "gui.help.k1", "gui.help.k8", "gui.time.now", "gui.time.min", "gui.time.week",
                "gui.sess.eta", "gui.badge.open", "gui.badge.proposed", "gui.copy_id",
                "gui.copied", "gui.tasks.empty", "gui.modal.close",
                "gui.toast.task_added", "gui.toast.task_done", "gui.toast.session_started",
                "gui.toast.session_stopped", "gui.toast.project_active", "gui.toast.git_synced",
                "gui.cmd.new_task", "gui.cmd.session", "gui.cmd.goto.stats"]:
        assert key in fr and key in en, f"missing key {key}"


def test_all_i18n_keys_used_in_js_exist_in_both_langs():
    from devdash.core import i18n
    keys = {k for k in re.findall(r'T\("([\w.]+)"', JS) if not k.endswith(".")}  # skip dynamic prefixes like T("prio." + x)
    for lang in ("fr", "en"):
        msgs = i18n.messages(lang)
        missing = sorted(k for k in keys if k not in msgs)
        assert not missing, f"{lang} catalog missing keys used in app.js: {missing}"
