"""Security + AI-provider flexibility tests (post-review round 2).

Security: no API key leakage (status/events/errors), Host+Origin enforcement
on mutating routes, ReDoS cap on commit-message parsing, DB file perms,
CSRF-relevant headers. AI: every provider family (ollama / local HTTP /
cli subprocess / cloud presets / mock), loopback-aware cloud detection,
and the local-only `ai context` preview.
"""

from __future__ import annotations

import json
import shlex
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from devdash.core import config as cfgmod
from devdash.core import db, events, gitwatch, projects, tasks
from devdash.core.ai import ai_status, get_provider, is_cloud
from devdash.core.ai import assistant
from devdash.core.ai.provider import AIError
from devdash.core.ai.providers.cli import CliProvider
from devdash.core.ai.providers.openai_compat import OpenAICompatProvider
from devdash.gui.api import Api
from devdash.gui.server import make_server


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home: Path):
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def project(conn):
    p = projects.create(conn, "Aether Vale", engine="unity")
    projects.set_active(conn, p.id)
    return p


def _enable_ai(home: Path, **over) -> cfgmod.Config:
    cfg = cfgmod.load_config(home)
    cfg.ai_enabled = True
    # reset overridable fields so each call is independent
    cfg.ai_base_url = over.pop("ai_base_url", "")
    cfg.ai_api_key = over.pop("ai_api_key", "")
    cfg.ai_cli_command = over.pop("ai_cli_command", "")
    for k, v in over.items():
        setattr(cfg, k, v)
    cfgmod.save_config(cfg, home)
    return cfg


# --- AI provider matrix --------------------------------------------------------

def test_provider_presets_and_cloud_detection(home):
    cfg = _enable_ai(home, ai_provider="local", ai_model="qwen3")
    p = get_provider(cfg)
    assert isinstance(p, OpenAICompatProvider) and p.name == "local"
    assert p.is_available()  # no API key needed for local servers
    assert p.base_url == "http://localhost:1234/v1"
    assert is_cloud(cfg) is False

    cfg = _enable_ai(home, ai_provider="local", ai_base_url="http://localhost:8080/v1")
    assert get_provider(cfg).base_url == "http://localhost:8080/v1"  # llama.cpp server
    assert is_cloud(cfg) is False

    cfg = _enable_ai(home, ai_provider="mistral", ai_model="mistral-small-latest",
                     ai_api_key="SECRET-KEY-123")
    p = get_provider(cfg)
    assert p.name == "mistral" and p.base_url == "https://api.mistral.ai/v1"
    assert is_cloud(cfg) is True

    cfg = _enable_ai(home, ai_provider="openai_compat",
                     ai_base_url="http://127.0.0.1:9999/v1", ai_model="m")
    assert is_cloud(cfg) is False  # loopback URL => not cloud even for openai_compat

    cfg = _enable_ai(home, ai_provider="ollama", ai_model="qwen3:8b")
    assert is_cloud(cfg) is False

    cfg = _enable_ai(home, ai_provider="mock", ai_model="mock")
    assert is_cloud(cfg) is False

    cfg = _enable_ai(home, ai_provider="cli", ai_cli_command="echo hi")
    assert isinstance(get_provider(cfg), CliProvider)
    assert is_cloud(cfg) is False

    cfg = _enable_ai(home, ai_provider="bogus")
    with pytest.raises(AIError):
        get_provider(cfg)

    cfg = cfgmod.load_config(home)
    cfg.ai_enabled = False
    assert get_provider(cfg) is None


def test_anthropic_provider(home, monkeypatch):
    """Claude via the Anthropic Messages API: preset, cloud flag, and the
    request translation (system prompt -> top-level field, x-api-key header)."""
    from devdash.core.ai.providers import anthropic as anth

    cfg = _enable_ai(home, ai_provider="anthropic", ai_model="claude-sonnet-4-5",
                     ai_api_key="sk-ant-SECRET")
    p = get_provider(cfg)
    assert isinstance(p, anth.AnthropicProvider) and p.name == "anthropic"
    assert p.base_url == "https://api.anthropic.com"
    assert is_cloud(cfg) is True  # cloud provider: GUI banner + CLI warning
    assert p.is_available() is True

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"content": [{"type": "text", "text": "bonjour "},
                                           {"type": "text", "text": "le monde"}]}).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(anth.urllib.request, "urlopen", fake_urlopen)
    answer = p.chat([
        {"role": "system", "content": "Tu es un assistant dev."},
        {"role": "user", "content": "salut"},
    ])
    assert answer == "bonjour le monde"  # text blocks concatenated
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"].get("X-api-key") == "sk-ant-SECRET"
    assert captured["headers"].get("Anthropic-version") == "2023-06-01"
    assert captured["body"]["system"] == "Tu es un assistant dev."  # system extracted
    assert captured["body"]["messages"] == [{"role": "user", "content": "salut"}]
    assert captured["body"]["max_tokens"] > 0
    # the key must never appear in raised errors either
    import io
    def boom(req, timeout=None):
        body = io.BytesIO(b'{"error":{"message":"invalid api key sk-ant-SECRET"}}')
        raise anth.urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, body)
    monkeypatch.setattr(anth.urllib.request, "urlopen", boom)
    with pytest.raises(AIError) as ei:
        p.chat([{"role": "user", "content": "x"}])
    assert "sk-ant-SECRET" not in str(ei.value) and "***" in str(ei.value)


def test_cli_provider_roundtrip(home, conn, project, tmp_path):
    """Any local CLI tool works: conversation JSON on stdin, answer on stdout."""
    script = tmp_path / "fake_ai.py"
    script.write_text(
        "import sys, json\n"
        "d = json.load(sys.stdin)\n"
        "print('ECHO:' + d['messages'][-1]['content'][:30])\n",
        encoding="utf-8",
    )
    cfg = _enable_ai(home, ai_provider="cli",
                     ai_cli_command=shlex.join([sys.executable, str(script)]))
    r = assistant.ask(conn, "où en suis-je ?", home=home)
    assert r["answer"].startswith("ECHO:")
    assert "où en suis-je" in r["answer"]
    assert r["cloud"] is False


def test_cli_provider_errors_are_clean(home):
    p = CliProvider("")
    assert p.is_available() is False
    with pytest.raises(AIError):
        p.chat([{"role": "user", "content": "x"}])
    p = CliProvider("definitely-not-a-real-binary-xyz --flag")
    with pytest.raises(AIError, match="not found"):
        p.chat([{"role": "user", "content": "x"}])


def test_openai_compat_local_needs_no_key(home):
    p = OpenAICompatProvider("http://localhost:1234/v1", "", "model", require_key=False)
    assert p.is_available() is True
    p2 = OpenAICompatProvider("https://api.mistral.ai/v1", "", "model")
    assert p2.is_available() is False
    with pytest.raises(AIError, match="ai_api_key"):
        p2.chat([{"role": "user", "content": "x"}])


# --- no secret leakage ----------------------------------------------------------

def test_api_key_never_leaks(home, conn, project):
    cfg = _enable_ai(home, ai_provider="mistral", ai_model="m", ai_api_key="SECRET-KEY-123")
    # ai_status (GUI/CLI) must not contain the key
    st = ai_status(cfg)
    assert "SECRET-KEY-123" not in json.dumps(st)
    # events payloads must not contain config/secrets either
    tasks.add(conn, project.id, "t", tags=["x"])
    all_events = events.since(conn, 0, limit=10000)
    assert "SECRET-KEY-123" not in json.dumps([e.to_dict() for e in all_events])
    # config file itself is chmod 600 (best effort on Windows)
    import stat
    mode = (home / "config.json").stat().st_mode
    if sys.platform != "win32":
        assert mode & 0o077 == 0, "config.json must not be group/world readable"


def test_context_preview_is_local_and_bounded(home, conn, project):
    tasks.add(conn, project.id, "Dash movement", priority="high", tags=["gameplay"])
    # works with AI DISABLED (pure local read)
    r = assistant.context_preview(conn, home=home)
    assert r["ai_enabled"] is False and r["cloud"] is False
    assert "Aether Vale" in r["context"] and "Dash movement" in r["context"]
    assert r["chars"] <= r["max_chars"]
    # API endpoint too
    api = Api(home=home)
    r2 = api.ai_context()
    assert r2["context"] == r["context"]


# --- ReDoS cap on commit parsing -------------------------------------------------

def test_parse_message_capped_and_safe():
    pats = cfgmod.DEFAULT_COMMIT_PATTERNS
    assert gitwatch.parse_message("fix: dash #42", pats) == (42, None)
    # huge hostile subject: capped, returns quickly, no match
    huge = "a" * 200_000
    assert gitwatch.parse_message(huge, pats) == (None, None)
    # invalid user pattern -> clean GitError, not a raw re.error
    with pytest.raises(gitwatch.GitError):
        gitwatch.parse_message("hello", ["(unclosed"])


# --- HTTP hardening: Origin check -------------------------------------------------

def _serve(home):
    srv = make_server(port=0, home=home)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _post(base, path, body, headers):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    return urllib.request.urlopen(req)


def test_server_origin_check(home):
    srv = _serve(home)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        # forged Origin from another site -> 403 even with a valid Host
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base, "/api/projects", {"name": "X"}, {"Origin": "http://evil.example"})
        assert e.value.code == 403
        # our own origin -> accepted
        r = _post(base, "/api/projects", {"name": "Ok"},
                  {"Origin": f"http://127.0.0.1:{port}"})
        assert json.loads(r.read())["slug"] == "ok"
        # security header present
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
    finally:
        srv.shutdown(); srv.server_close()


def test_db_connect_restricts_perms_without_crashing(home):
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    c.close()
    if sys.platform != "win32":
        mode = (home / "devdash.db").stat().st_mode
        assert mode & 0o077 == 0
