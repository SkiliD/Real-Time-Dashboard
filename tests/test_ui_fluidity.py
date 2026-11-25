"""Static checks for the GUI fluidity pass (animated close, tab transitions, toasts)."""
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "devdash" / "gui" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
STYLES = (STATIC / "styles.css").read_text(encoding="utf-8")


def test_hide_animated_helper_and_all_closers_use_it():
    assert "function hideAnimated(el)" in APP_JS
    for fn in ("closeTaskModal", "closeFeatModal", "closeModal", "closeEditModal", "closePalette", "closeHelp"):
        line = next(l for l in APP_JS.splitlines() if l.startswith(f"function {fn}("))
        assert "hideAnimated(" in line, f"{fn} must close via hideAnimated"


def test_tab_switch_animates_only_target_page():
    assert "switchTab" in APP_JS
    assert '.tab-page.enter' in APP_JS or 'classList.add("enter")' in APP_JS
    assert "@keyframes page-in" in STYLES
    assert ".tab-page.enter" in STYLES


def test_closing_overlay_animation():
    assert "@keyframes fadeout" in STYLES
    assert ".closing" in STYLES


def test_toast_exit_animation():
    assert "#toast.out" in STYLES
    assert "@keyframes toastout" in STYLES
    assert 'classList.add("out")' in APP_JS


def test_new_task_cards_land_animation():
    assert "seenTaskIds" in APP_JS
    assert 'classList.add("landed")' in APP_JS
    assert ".landed" in STYLES or "landed" in STYLES


def test_new_task_refocuses_input_for_rapid_entry():
    assert "#new-task-title" in APP_JS
    # addTask focuses the title again after adding so the user can chain tasks
    add_idx = APP_JS.index("async function addTask(")
    seg = APP_JS[add_idx:add_idx + 4000]
    assert 'focus()' in seg


INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def test_tab_activity_dots_in_html_and_logic():
    assert 'id="badge-git"' in INDEX_HTML and 'id="badge-timeline"' in INDEX_HTML
    assert "tab-dot" in INDEX_HTML
    assert "function updateTabDots()" in APP_JS
    assert "gitSeenHash" in APP_JS and "tlSeenId" in APP_JS
    # visiting a tab clears its dot
    assert 'state.gitSeenHash = newest.hash' in APP_JS
    assert "state.tlSeenId = maxTlId()" in APP_JS
    assert ".tab-dot" in STYLES


def test_running_task_highlight():
    assert '" running"' in APP_JS  # card.className gains .running
    assert "state.session.task_id === t.id" in APP_JS
    assert ".card.running" in STYLES
    assert "@keyframes runpulse" in STYLES
    # kanban render signature includes the running task id so it re-renders on session start/stop
    assert "state.session ? state.session.task_id : null" in APP_JS


def test_column_counter_more_visible():
    assert ".col .count" in STYLES
    assert "font-weight: 600" in STYLES


def test_new_badge_i18n_keys_in_both_catalogs():
    from devdash.core import i18n
    assert set(i18n.FR) == set(i18n.EN)
    for key in ("gui.badge.git_new", "gui.badge.tl_new", "gui.last_update"):
        assert key in i18n.FR and key in i18n.EN


def test_window_title_shows_active_project_and_timer():
    assert "function updateTitle()" in APP_JS
    assert "DevDash · ${p.name}" in APP_JS  # project name in the window/tab title
    assert "⏱ " in APP_JS  # live timer prefix while a session runs
    assert "updateTitle();" in APP_JS  # refreshed from render() and tickClock


def test_last_update_hint():
    assert 'id="last-update"' in INDEX_HTML
    assert "state.lastLoadAt = Date.now();" in APP_JS
    assert 'T("gui.last_update", { s })' in APP_JS


def test_button_press_feedback():
    assert ".btn:active, .mini-btn:active, .tab:active" in STYLES
    assert "transform: scale(.96)" in STYLES


def test_done_flash_animation():
    assert "justDoneIds" in APP_JS
    assert 'card.classList.add("done-flash")' in APP_JS
    assert ".card.done-flash" in STYLES
    assert "@keyframes doneflash" in STYLES


def test_trash_view_has_proper_empty_state_and_hides_sessions():
    assert 'id="trash-empty"' in INDEX_HTML and 'class="empty-icon"' in INDEX_HTML
    assert 'id="trash-head"' in INDEX_HTML and 'id="trash-count"' in INDEX_HTML
    assert 'empty.classList.remove("hidden")' in APP_JS
    # trash is a focused view: the recent-sessions block underneath is hidden
    assert '$("#sessions-section").classList.toggle("hidden", !!state.showTrash)' in APP_JS
    assert "#trash-empty" in STYLES and "border: 1px dashed var(--border)" in STYLES
    from devdash.core import i18n
    assert "gui.trash.empty_hint" in i18n.FR and "gui.trash.empty_hint" in i18n.EN
    assert set(i18n.FR) == set(i18n.EN)


def test_trash_is_a_real_toggle_button_not_a_checkbox():
    assert 'id="btn-trash"' in INDEX_HTML and 'type="button"' in INDEX_HTML
    assert 'id="filter-trash"' not in INDEX_HTML  # old checkbox gone
    assert 'aria-pressed="false"' in INDEX_HTML
    assert 'class="tool-toggle"' in INDEX_HTML  # styled like the toolbar selects, not a .btn
    assert '$("#btn-trash").onclick' in APP_JS
    assert 'tb.classList.toggle("btn-active"' in APP_JS  # active state synced in renderKanban
    assert "#task-toolbar .tool-toggle" in STYLES  # shares the select styling rule
    assert "#task-toolbar .tool-toggle.btn-active" in STYLES  # visible "on" state


def test_kanban_project_switch_uses_slug():
    assert "renderKanban._pid !== state.active)" in APP_JS  # state.active is a slug, not an object


def test_render_signatures_cover_tasks_and_git_error():
    assert "state.gitError, state.tasks], renderGit);" in APP_JS
    assert 'renderPanel("features", [state.features, state.tasks], renderFeatures);' in APP_JS


def test_tab_shortcut_covers_8_tabs_and_estimate_zero_chip():
    assert 'case "8":' in APP_JS  # AI tab makes 8 visible tabs
    assert "t.estimate_min != null ?" in APP_JS  # 0 min estimate still shows its chip


def test_detect_folder_stale_response_guard():
    assert "const seq = ++detectSeq;" in APP_JS and "if (seq !== detectSeq) return;" in APP_JS


def test_tour_flag_server_is_authoritative():
    assert 'api.get("/api/tour").then((r) => { state.tourDone = !!r.done; })' in APP_JS
