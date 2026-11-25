# tests -- AI provider presets (OpenRouter, model catalog), richer context, MCP context tool
# Works on Windows, Mac and Linux.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, projects as proj, tasks
from devdash.core.ai import (
    PROVIDER_CATALOG, PROVIDER_MODELS, apply_ai_settings, base_url_for,
    get_provider, is_cloud, provider_catalog,
)
from devdash.core.ai.context import build_context
from devdash.core.ai.providers.openai_compat import OpenAICompatProvider
from devdash import mcp_server


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home):
    c = db.connect(cfgmod.db_path(home))
    db.init_db(c)
    yield c
    c.close()


def _cfg(**kw) -> cfgmod.Config:
    c = cfgmod.Config()
    c.ai_enabled = True
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# --- provider catalog: OpenRouter + model presets -----------------------------

def test_catalog_has_openrouter_with_models():
    cat = {p["id"]: p for p in provider_catalog()}
    assert "openrouter" in cat
    orp = cat["openrouter"]
    assert orp["scope"] == "cloud" and orp["needs_key"] and orp["needs_model"]
    assert orp["default_base_url"] == "https://openrouter.ai/api/v1"
    assert len(orp["models"]) >= 5  # preset dropdown, not a free-text field
    ids = {m["id"] for m in orp["models"]}
    assert "anthropic/claude-sonnet-4.5" in ids and "openai/gpt-5" in ids
    # every catalog entry exposes a models list (possibly empty = free text)
    assert all("models" in p for p in provider_catalog())
    # local providers stay free-text (their models are dynamic)
    assert cat["ollama"]["models"] == []
    # the static catalog and the model map agree
    assert set(PROVIDER_MODELS) >= {p["id"] for p in PROVIDER_CATALOG}


def test_openrouter_provider_wiring():
    cfg = _cfg(ai_provider="openrouter", ai_model="anthropic/claude-sonnet-4.5", ai_api_key="sk-or-x")
    assert base_url_for(cfg, "openrouter") == "https://openrouter.ai/api/v1"
    assert is_cloud(cfg) is True  # remote by default
    p = get_provider(cfg)
    assert isinstance(p, OpenAICompatProvider) and p.name == "openrouter"
    assert p.base_url == "https://openrouter.ai/api/v1" and p.model == "anthropic/claude-sonnet-4.5"
    # a loopback override makes it "local" (self-hosted gateway) — no cloud warning
    cfg2 = _cfg(ai_provider="openrouter", ai_base_url="http://localhost:9999/v1")
    assert is_cloud(cfg2) is False
    assert base_url_for(cfg2, "openrouter") == "http://localhost:9999/v1"


def test_apply_ai_settings_accepts_openrouter_rejects_unknown():
    cfg = cfgmod.Config()
    out = apply_ai_settings(cfg, {"ai_provider": "openrouter", "ai_model": "openai/gpt-5",
                                  "ai_api_key": "sk-or-123"})
    assert out.ai_provider == "openrouter" and out.ai_model == "openai/gpt-5"
    assert out.ai_api_key == "sk-or-123"
    with pytest.raises(ValueError):
        apply_ai_settings(cfg, {"ai_provider": "skynet"})
    # empty key keeps the stored one (write-only semantics)
    out2 = apply_ai_settings(cfg, {"ai_api_key": ""})
    assert out2.ai_api_key == "sk-or-123"


# --- richer context sent to the model ------------------------------------------

def test_build_context_includes_tree_and_commits(conn, home, tmp_path):
    p = proj.create(conn, "Game", engine="unity")
    proj.set_active(conn, p.id)
    epic = tasks.add(conn, p.id, "Epic HUD")
    tasks.add(conn, p.id, "Refonte barre de vie", parent_id=epic.id)
    tasks.add(conn, p.id, "Menu pause")
    tasks.done(conn, tasks.list(conn, parent_id=epic.id)[0].id)
    # fake git cache rows so "Commits récents" appears without a real repo
    repo = str(tmp_path / "repo")
    conn.execute(
        "INSERT INTO git_links(project_id, repo_path, added_at) VALUES (?, ?, ?)",
        (p.id, repo, "2026-11-23T00:00:00+00:00"))
    conn.execute(
        "INSERT INTO git_commits(repo_path, hash, short, author, message, branch, committed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (repo, "a" * 40, "abc1234", "me", "feat: HUD #" + str(epic.id), "main", "2026-11-23T10:00:00+00:00"))
    conn.commit()
    ctx = build_context(conn)
    assert "Arborescence des tâches ouvertes" in ctx
    assert f"#{epic.id} Epic HUD" in ctx and "1/1 sous-tâches faites" in ctx
    assert "Commits récents" in ctx and "abc1234" in ctx


# --- MCP: devdash_context tool --------------------------------------------------

def test_mcp_context_tool(conn, home):
    p = proj.create(conn, "Game", engine="unity")
    proj.set_active(conn, p.id)
    epic = tasks.add(conn, p.id, "Epic")
    tasks.add(conn, p.id, "Sub", parent_id=epic.id)
    r = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, home)
    names = [t["name"] for t in r["result"]["tools"]]
    assert "devdash_context" in names
    r = mcp_server.handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "devdash_context", "arguments": {}}}, home)
    res = r["result"]["content"][0]["text"]
    data = json.loads(res)
    assert data["project"]["name"] == "Game"
    assert data["tasks"][0]["title"] == "Epic"
    assert data["tasks"][0]["children"][0]["title"] == "Sub"
    assert data["tasks"][0]["progress"] == {"total": 1, "done": 0, "pct": 0}
    assert "git" in data and "recent_logs" in data and "streak_days" in data


# --- CLI: mcp-config snippets are valid ----------------------------------------

def test_mcp_config_snippets_valid():
    from devdash.cli.main import _MCP_SNIPPETS
    assert {"vscode", "cursor", "vibe", "claude-desktop"} <= set(_MCP_SNIPPETS)
    for editor, snip in _MCP_SNIPPETS.items():
        text = snip["json"].replace("{python}", "python")
        if editor == "vibe":
            assert 'name = "devdash"' in text and '"-m", "devdash", "mcp"' in text
        else:
            data = json.loads(text)  # must be valid JSON for the editor
            key = "servers" if editor == "vscode" else "mcpServers"
            assert data[key]["devdash"]["args"] == ["-m", "devdash", "mcp"]
