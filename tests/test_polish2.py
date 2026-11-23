"""tests -- polish2

If it breaks, the tests will say so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "devdash" / "gui" / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "devdash" / "gui" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "devdash" / "gui" / "static" / "styles.css").read_text(encoding="utf-8")
SRV = (ROOT / "devdash" / "gui" / "server.py").read_text(encoding="utf-8")


def test_new_elements_in_html_and_referenced_in_js():
    for el in ["header-progress", "header-progress-bar", "btn-stats-png", "btn-open-home", "task-time"]:
        assert f'id="{el}"' in HTML, el
        assert f'$("#{el}")' in JS, el


def test_polish_hooks_in_js():
    for needle in ['document.title', "Notification.requestPermission", "sessionNotifyId",
                   "timeByTask", "commit-hash", "toDataURL", "loadTaskMinutes",
                   'api.post("/api/open-home"']:
        assert needle in JS, needle


def test_polish_css():
    for sel in ["#header-progress-bar", ".commit-hash", "var(--proj)"]:
        assert sel in CSS, sel


def test_favicon_present():
    assert "rel=\"icon\"" in HTML and "data:image/svg+xml" in HTML


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def test_open_home_endpoint_safe_by_default(home, monkeypatch):
    # without open=true the endpoint must NOT touch the OS (no explorer popping
    # up during tests); with open=true it would — so tests only check the path
    from devdash.gui.api import Api
    api = Api(home=home)
    r = api.open_home({})  # no side effect
    assert r["path"].endswith("devdash") or str(home) in r["path"]
    assert '"/api/open-home"' in SRV
