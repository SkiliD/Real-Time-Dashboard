# tests -- the REST layer is split into gui/api*.py mixins; these guards keep
# the facade (gui/api.py) and the route tables (gui/server.py) in sync.

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "devdash" / "gui"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def _api_sources() -> str:
    return "\n".join(f.read_text(encoding="utf-8") for f in sorted(GUI.glob("api*.py")))


def test_api_is_a_facade_of_mixins():
    from devdash.gui.api import Api
    facade = (GUI / "api.py").read_text(encoding="utf-8")
    assert "class Api(" in facade and "Mixin" in facade
    # the facade itself must stay small: domain endpoints live in api_<domain>.py
    assert len(facade.splitlines()) < 260
    for mod in ("api_projects", "api_tasks", "api_features", "api_git", "api_files",
                "api_logs", "api_sessions", "api_stats", "api_ai", "api_settings",
                "api_system", "api_misc", "api_common"):
        assert (GUI / f"{mod}.py").is_file(), f"missing mixin module {mod}.py"
    assert callable(Api.projects_create) and callable(Api.git_run) and callable(Api.ai_ask)


def test_every_route_lambda_targets_an_existing_api_method():
    from devdash.gui.api import Api
    srv = (GUI / "server.py").read_text(encoding="utf-8")
    called = set(re.findall(r"\bapi\.(\w+)\(", srv))
    assert called, "no api.<method>(...) calls found in server.py"
    missing = [name for name in called if not callable(getattr(Api, name, None))]
    assert not missing, f"routes call missing Api methods: {missing}"


def test_no_duplicate_endpoint_methods_across_mixins():
    seen = {}
    for f in sorted(GUI.glob("api*.py")):
        for m in re.findall(r"^    (?:async )?def (\w+)\(", f.read_text(encoding="utf-8"), re.M):
            if m.startswith("_"):
                continue  # dunder + private helpers may legitimately repeat (e.g. ApiError.__init__)
            assert m not in seen, f"method {m} defined in both {seen[m]} and {f.name}"
            seen[m] = f.name


def test_events_since_returns_last_id_of_returned_events(home):
    # regression: the split once truncated this tail — it must return a dict
    from devdash.gui.api import Api
    api = Api(home=home)
    api.projects_create({"name": "Trunc", "engine": "web", "set_active": True})
    ev = api.events_since(0)
    assert isinstance(ev, dict) and "events" in ev and "last_id" in ev
    assert ev["last_id"] >= 1 and len(ev["events"]) >= 1
