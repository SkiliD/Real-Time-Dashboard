# tests -- keyboard handling inside kanban cards: typing in the inline subtask
# input (or any field/button inside a card) must never trigger the card's own
# Enter/Space shortcut (which opens the task edit modal). Regression: pressing
# Space while writing a new subtask opened the parent task's edit modal.

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "devdash" / "gui" / "static" / "app.js").read_text(encoding="utf-8")


def _fn_body(src: str, signature: str) -> str:
    i = src.index(signature)
    rest = src[i:]
    ends = [rest.find("\nfunction ", 10), rest.find("\nasync function ", 10)]
    ends = [e for e in ends if e > 0]
    return rest[:min(ends) if ends else len(rest)]


def test_card_keydown_ignores_events_from_fields_and_buttons():
    body = _fn_body(APP_JS, "function taskCard(t)")
    kd = body[body.index('card.addEventListener("keydown"'):]
    kd = kd[:kd.index("});") + 3]
    assert 'e.target.closest("input, textarea, select, button")' in kd, \
        "card keydown must ignore events coming from inner fields/buttons"
    assert 'openTaskModal(t)' in kd  # the shortcut itself still works on the card body


def test_subtask_input_stops_propagation_for_all_keys():
    body = _fn_body(APP_JS, "function taskCard(t)")
    i = body.index('input.addEventListener("keydown"')
    kd = body[i:body.index("});", i) + 3]
    # stopPropagation must run BEFORE the Enter check, for every key (Space too)
    assert kd.index("e.stopPropagation()") < kd.index('e.key !== "Enter"')


def test_global_shortcuts_ignore_typing_targets():
    i = APP_JS.index('document.addEventListener("keydown"')
    body = APP_JS[i:]
    assert "const typing =" in body and "if (typing) return;" in body, \
        "plain-key shortcuts (n, s, 1-7…) must not fire while typing in a field"
