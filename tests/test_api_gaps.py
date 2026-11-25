# tests -- HTTP-layer coverage for endpoints that were previously only covered at
# core level (features lifecycle, AI context/summarize/verdict, tags, git
# repos/unlink, files) + a loop over every MCP tool so none can rot silently.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, projects as proj
from devdash.core.ai import assistant
from devdash.gui.api import Api, ApiError


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def api(home: Path) -> Api:
    a = Api(home=home)
    a.projects_create({"name": "Game", "engine": "unity", "set_active": True})
    return a


def _enable_mock_ai(home: Path) -> None:
    cfgmod.save_config(
        cfgmod.load_config(home)._replace(ai_enabled=True, ai_provider="mock", ai_model="mock"),
        home,
    ) if hasattr(cfgmod.load_config(home), "_replace") else None
    # simpler: write config.json directly (dataclass may vary)
    data = {"ai_enabled": True, "ai_provider": "mock", "ai_model": "mock"}
    cfgmod.config_path(home).parent.mkdir(parents=True, exist_ok=True)
    cfgmod.config_path(home).write_text(json.dumps(data), encoding="utf-8")


# --- features lifecycle over the HTTP layer -----------------------------------

def test_features_lifecycle_over_api(api: Api):
    f = api.features_add({"name": "dash"})
    t = api.tasks_add({"title": "Dash impl"})
    r = api.features_attach({"ref": f["id"], "task_id": t["id"]})
    assert r["progress"]["total"] == 1

    # propose: confidence validated (400 on non-number, clamped to [0,1])
    r = api.features_propose({"ref": f["id"], "confidence": 0.9, "reasoning": "looks done"})
    assert r["status"] == "proposed" and r["confidence"] == 0.9
    with pytest.raises(ApiError) as e:
        api.features_propose({"ref": f["id"], "confidence": "high"})
    assert e.value.status == 400
    r = api.features_propose({"ref": f["id"], "confidence": 5})
    assert r["confidence"] == 1.0  # clamped, never >100% in the GUI

    api.features_reject({"ref": f["id"]})
    r = api.features_reopen({"ref": f["id"]})
    assert r["status"] == "open"
    r = api.features_update({"ref": f["id"], "name": "air-dash"})
    assert r["name"] == "air-dash"
    r = api.features_approve({"ref": f["id"], "note": "shipped"})
    assert r["status"] == "done"
    r = api.features_detach({"ref": f["id"], "task_id": t["id"]})
    assert r["progress"]["total"] == 0
    r = api.features_delete({"ref": f["id"]})
    assert r.get("deleted") in (True, None) or r["status"] in ("deleted", "done")


def test_features_errors_are_clean_400_404(api: Api):
    with pytest.raises(ApiError) as e:
        api.features_approve({"ref": 999})
    assert e.value.status == 404
    with pytest.raises(ApiError) as e2:
        api.features_propose({"ref": "nope"})
    assert e2.value.status in (400, 404)


# --- AI endpoints over the HTTP layer (mock provider) --------------------------

def test_ai_endpoints_over_api(home: Path, api: Api):
    _enable_mock_ai(home)
    f = api.features_add({"name": "dash"})
    ctx = api.ai_context()
    assert "context" in ctx and ctx.get("provider") == "mock"
    s = api.ai_summarize({})
    assert "summary" in s
    v = api.ai_verdict({"ref": f["id"]})
    # verdict is normalized server-side: confidence always a clamped float
    assert 0.0 <= v["verdict"]["confidence"] <= 1.0
    assert isinstance(v["verdict"]["reasoning"], str)


def test_normalize_verdict_never_nan():
    v = assistant._normalize_verdict({"seems_complete": True})  # no confidence
    assert v["confidence"] == 0.0
    v = assistant._normalize_verdict({"confidence": "high", "reasoning": 42, "evidence": [1, "a", None]})
    assert v["confidence"] == 0.0 and v["reasoning"] == "" and v["evidence"] == ["a"]
    v = assistant._normalize_verdict({"confidence": 7})
    assert v["confidence"] == 1.0


# --- tags / git repos / unlink / files -----------------------------------------

def test_tags_git_repos_unlink_files_over_api(api: Api):
    api.tasks_add({"title": "Dash", "tags": ["gameplay", "ui"]})
    tags = api.tags({})
    assert "gameplay" in tags["tags"] and "ui" in tags["tags"]
    repos = api.git_repos()
    assert "repos" in repos  # empty list is fine: no repo linked
    r = api.git_unlink({})  # unlink without a linked repo is a clean no-op
    assert r["linked"] is False
    files = api.files_list()
    assert isinstance(files, dict)


# --- every MCP tool answers (structured result, never a crash) -----------------

def test_mcp_all_tools_respond(home: Path):
    from devdash.mcp_server import TOOLS, _call

    c = db.connect(cfgmod.db_path(home))
    db.init_db(c)
    p = proj.create(c, "Game", engine="unity")
    proj.set_active(c, p.id)
    c.close()

    args_for = {
        "devdash_task_add": {"title": "mcp task"},
        "devdash_task_set_status": {"id": 1, "status": "done"},
        "devdash_log_add": {"content": "mcp log"},
        "devdash_log_search": {"query": "mcp"},
        "devdash_stats": {"period": "week"},
        "devdash_git_run": {"cmd": "status"},
        "devdash_git_remote": {"cmd": "remote -v"},
        "devdash_ai_ask": {"question": "where do I stand?"},
        "devdash_task_verify": {"id": 1},
    }
    from devdash.core.ai.provider import AIDisabledError, AIError

    clean_errors = (KeyError, ValueError, AIDisabledError, AIError)  # typed, no stack-trace leak
    for t in TOOLS:
        try:
            r = _call(t["name"], args_for.get(t["name"], {}), home)
            assert isinstance(r, dict), t["name"]  # success: structured payload
        except clean_errors:
            pass  # legitimate structured error (no repo linked, AI off, ...): fine
        # anything else would be an unhandled crash -> test fails


def test_list_payloads_truncate_long_text_but_show_returns_full(api: Api):
    # /api/tasks and /api/logs are polled every 1.5 s: long fields must be truncated
    # in list payloads; the full text is served by the /show endpoints
    from devdash.gui.api_common import LIST_DESC_MAX, LIST_CONTENT_MAX
    long_desc = "x" * (LIST_DESC_MAX + 500)
    long_content = "y" * (LIST_CONTENT_MAX + 700)
    t = api.tasks_add({"title": "Big", "description": long_desc})
    e = api.logs_add({"content": long_content})
    lst = api.tasks({"all": "1"})
    row = next(r for r in lst["tasks"] if r["id"] == t["id"])
    assert row["description_truncated"] is True and len(row["description"]) <= LIST_DESC_MAX + 1
    assert api.tasks_show({"id": t["id"]})["task"]["description"] == long_desc
    ll = api.logs_list({"limit": "50"})
    erow = next(r for r in ll["entries"] if r["id"] == e["id"])
    assert erow["content_truncated"] is True and len(erow["content"]) <= LIST_CONTENT_MAX + 1
    assert api.logs_show({"id": e["id"]})["entry"]["content"] == long_content
    # short text is untouched, no flag
    t2 = api.tasks_add({"title": "Small", "description": "court"})
    row2 = next(r for r in api.tasks({"all": "1"})["tasks"] if r["id"] == t2["id"])
    assert row2["description"] == "court" and "description_truncated" not in row2
    # show endpoints validate input
    with pytest.raises(ApiError) as e1:
        api.tasks_show({})
    assert e1.value.status == 400
    with pytest.raises(ApiError) as e2:
        api.logs_show({"id": 999999})
    assert e2.value.status == 404


def test_show_routes_registered():
    from devdash.gui.server import GET_ROUTES
    assert "/api/tasks/show" in GET_ROUTES and "/api/logs/show" in GET_ROUTES


def test_gui_fetches_full_text_before_editing():
    js = (Path(__file__).resolve().parent.parent / "devdash/gui/static/app.js").read_text(encoding="utf-8")
    assert 'api.get("/api/tasks/show?id=" + t.id)' in js   # task modal: full description
    assert 'api.get("/api/logs/show?id=" + e.id)' in js     # log inline edit: full content
