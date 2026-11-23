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


def test_all_modal_overlays_are_fixed_positioned():
    # a modal that renders in page flow appears "below the page": every modal
    # container must share the fixed overlay rule
    m = re.search(r"(#modal[^{]*)\{\s*position:\s*fixed", CSS)
    assert m, "no fixed overlay rule found"
    selector = m.group(1)
    for mid in ["#modal", "#modal-edit", "#modal-task", "#modal-help"]:
        assert mid in selector, f"{mid} missing from the fixed overlay selector"
    assert "max-height" in CSS, "modal-card must scroll when taller than the viewport"


def test_app_js_uses_new_ux_hooks():
    for needle in ["openPalette", "closePalette", "paletteKey", "timeAgo(", "notify(",
                   "saveUi(", "loadUi(", 'addEventListener("keydown"', "localStorage",
                   "btn-copy-task-id", "session-eta"]:
        assert needle in JS, f"app.js missing {needle}"


def test_switching_to_settings_tab_loads_the_forms():
    # settings are loaded lazily (skipped on polls from other tabs), so switchTab
    # itself must trigger the load — otherwise the form renders empty
    m = re.search(r"function switchTab\(tab\) \{(.*?)\n\}", JS, flags=re.S)
    assert m, "switchTab not found"
    body = m.group(1)
    assert 'tab === "settings"' in body
    for fn in ["loadAiConfig(", "loadSettings(", "loadUpdateStatus("]:
        assert fn in body, f"switchTab does not call {fn}"


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
