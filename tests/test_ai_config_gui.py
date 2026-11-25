"""tests -- ai config gui"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from devdash.core import config as cfgmod
from devdash.gui.api import Api, ApiError
from devdash.gui.server import make_server


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


# --- provider catalog ------------------------------------------------------

def test_provider_catalog_is_public_metadata(home):
    api = Api(home=home)
    provs = api.ai_providers()["providers"]
    ids = {p["id"] for p in provs}
    assert {"ollama", "local", "cli", "mock", "anthropic", "mistral", "openai", "openai_compat",
            "openrouter"} <= ids
    allowed_fields = {"id", "scope", "needs_model", "needs_key", "needs_base_url",
                      "needs_cli_command", "default_base_url", "models"}
    for p in provs:
        assert p["scope"] in ("local", "cloud")
        assert set(p) <= allowed_fields  # metadata only: no secret material in the catalog
        # models = [{id, label}] preset list for the dropdown; never a secret
        assert all(set(m) == {"id", "label"} for m in p["models"])
    # local providers are flagged local, cloud ones cloud
    by_id = {p["id"]: p for p in provs}
    assert by_id["ollama"]["scope"] == "local" and by_id["cli"]["scope"] == "local"
    assert by_id["anthropic"]["scope"] == "cloud" and by_id["mistral"]["needs_key"] is True


# --- config read: key is write-only ----------------------------------------

def test_ai_config_get_never_returns_the_key(home):
    cfg = cfgmod.load_config(home)
    cfg.ai_enabled = True
    cfg.ai_provider = "mistral"
    cfg.ai_api_key = "sk-test-SECRET-123"
    cfgmod.save_config(cfg, home)

    api = Api(home=home)
    view = api.ai_config_get()
    assert view["api_key_set"] is True
    assert "sk-test-SECRET-123" not in json.dumps(view)
    assert "ai_api_key" not in view
    assert view["ai_provider"] == "mistral"
    assert view["cloud"] is True  # mistral preset URL is remote
    assert view["config_path"].endswith("config.json")


def test_ai_config_get_cloud_flag_local_vs_cloud(home):
    api = Api(home=home)
    cfg = cfgmod.load_config(home)
    cfg.ai_enabled = True
    cfg.ai_provider = "ollama"
    cfgmod.save_config(cfg, home)
    assert api.ai_config_get()["cloud"] is False
    cfg.ai_provider = "openai_compat"
    cfg.ai_base_url = "http://localhost:9999/v1"
    cfgmod.save_config(cfg, home)
    assert api.ai_config_get()["cloud"] is False  # loopback override = local
    cfg.ai_base_url = "https://api.example.com/v1"
    cfgmod.save_config(cfg, home)
    assert api.ai_config_get()["cloud"] is True


# --- config save: validation + key handling ---------------------------------

def test_ai_config_save_updates_and_keeps_key_when_empty(home):
    cfg = cfgmod.load_config(home)
    cfg.ai_api_key = "sk-keep-me"
    cfgmod.save_config(cfg, home)

    api = Api(home=home)
    r = api.ai_config_save({"ai_enabled": True, "ai_provider": "ollama",
                            "ai_model": "qwen3:8b", "ai_api_key": ""})  # empty = keep
    assert r["saved"] is True
    on_disk = json.loads(cfgmod.config_path(home).read_text(encoding="utf-8"))
    assert on_disk["ai_api_key"] == "sk-keep-me"      # preserved
    assert on_disk["ai_provider"] == "ollama" and on_disk["ai_model"] == "qwen3:8b"
    assert on_disk["ai_enabled"] is True
    assert "sk-keep-me" not in json.dumps(r)           # not echoed in the response
    # status endpoint reflects the change immediately
    assert api.ai_status()["enabled"] is True and api.ai_status()["provider"] == "ollama"


def test_ai_config_save_sets_new_key_and_masks_it(home):
    api = Api(home=home)
    r = api.ai_config_save({"ai_enabled": True, "ai_provider": "anthropic",
                            "ai_model": "claude-sonnet-4-5", "ai_api_key": "sk-ant-NEW"})
    assert "sk-ant-NEW" not in json.dumps(r)
    on_disk = json.loads(cfgmod.config_path(home).read_text(encoding="utf-8"))
    assert on_disk["ai_api_key"] == "sk-ant-NEW"
    assert api.ai_config_get()["api_key_set"] is True
    # config file perms: owner-only where the OS supports chmod
    if os.name == "posix":
        assert (cfgmod.config_path(home).stat().st_mode & 0o777) == 0o600


def test_ai_config_save_validation_errors_are_400(home):
    api = Api(home=home)
    with pytest.raises(ApiError) as e1:
        api.ai_config_save({"ai_provider": "not-a-provider"})
    assert e1.value.status == 400
    with pytest.raises(ApiError) as e2:
        api.ai_config_save({"ai_base_url": "ftp://nope"})
    assert e2.value.status == 400 and "http" in str(e2.value)
    with pytest.raises(ApiError) as e3:
        api.ai_config_save({"ai_context_max_chars": 10})
    assert e3.value.status == 400
    with pytest.raises(ApiError) as e4:
        api.ai_config_save({"ai_enabled": "yes"})
    assert e4.value.status == 400
    with pytest.raises(ApiError) as e5:
        api.ai_config_save({"ai_feature_flags": {"chat": "yes"}})
    assert e5.value.status == 400


def test_ai_config_save_partial_flags(home):
    api = Api(home=home)
    api.ai_config_save({"ai_feature_flags": {"plan": False}})
    flags = api.ai_config_get()["ai_feature_flags"]
    assert flags["plan"] is False and flags["chat"] is True  # others untouched


# --- connection dry-run ------------------------------------------------------

def test_ai_config_test_mock_provider(home):
    api = Api(home=home)
    r = api.ai_config_test({"ai_enabled": True, "ai_provider": "mock", "ai_model": ""})
    assert r["ok"] is True and r["provider"] == "mock" and r["cloud"] is False
    assert r["reply"]


def test_ai_config_test_requires_enabled_and_scrubs_key(home):
    api = Api(home=home)
    cfg = cfgmod.load_config(home)
    cfg.ai_enabled = True
    cfg.ai_provider = "ollama"
    cfg.ai_model = "qwen3:8b"
    cfg.ai_api_key = "sk-SECRET-scrub-me"
    cfgmod.save_config(cfg, home)

    # disabled -> clean 400
    with pytest.raises(ApiError) as e0:
        api.ai_config_test({"ai_enabled": False})
    assert e0.value.status == 400

    # no ollama server running here -> connection error, and the key must not leak
    with pytest.raises(ApiError) as e1:
        api.ai_config_test({"ai_enabled": True, "ai_provider": "ollama", "ai_model": "qwen3:8b"})
    assert e1.value.status == 400
    assert "sk-SECRET-scrub-me" not in str(e1.value)


def test_ai_config_test_does_not_persist(home):
    api = Api(home=home)
    api.ai_config_test({"ai_enabled": True, "ai_provider": "mock"})
    on_disk = json.loads(cfgmod.config_path(home).read_text(encoding="utf-8")) if cfgmod.config_path(home).exists() else {}
    assert on_disk.get("ai_enabled") in (None, False)  # dry run: nothing saved


# --- HTTP end-to-end ----------------------------------------------------------

def _post(base, path, body, host=None):
    headers = {"Content-Type": "application/json"}
    if host:
        headers["Host"] = host  # only to simulate a forged Host
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    return urllib.request.urlopen(req)


def test_http_ai_config_flow(home):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        cfg = json.loads(urllib.request.urlopen(base + "/api/ai/config").read())
        assert cfg["ai_enabled"] is False and cfg["api_key_set"] is False

        provs = json.loads(urllib.request.urlopen(base + "/api/ai/providers").read())["providers"]
        assert any(p["id"] == "ollama" for p in provs)

        r = json.loads(_post(base, "/api/ai/config", {
            "ai_enabled": True, "ai_provider": "mock", "ai_api_key": "",
        }).read())
        assert r["saved"] is True and r["status"]["enabled"] is True

        st = json.loads(urllib.request.urlopen(base + "/api/ai/status").read())
        assert st["enabled"] and st["provider"] == "mock" and st["cloud"] is False

        t = json.loads(_post(base, "/api/ai/test", {"ai_enabled": True, "ai_provider": "mock"}).read())
        assert t["ok"] is True

        # bad provider -> HTTP 400 with a JSON error, not a 500
        try:
            _post(base, "/api/ai/config", {"ai_provider": "nope"})
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400 and "ai_provider inconnu" in e.read().decode()  # localized err.ai_provider_unknown (default lang fr)

        # wrong Host -> 403 (CSRF guard also covers the new routes)
        try:
            _post(base, "/api/ai/config", {"ai_provider": "mock"}, host="evil.example.com")
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        srv.shutdown()
        srv.server_close()
