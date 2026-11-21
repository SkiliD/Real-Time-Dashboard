"""Optional AI assistant layer.

Fully optional: with `ai_enabled = false` (default) in config.json,
`get_provider()` returns None and every AI feature cleanly reports disabled.

Providers are interchangeable, chosen by `ai_provider` in config.json:

  LOCAL (nothing leaves the machine — "cloud" flag stays false):
    "ollama"   Ollama HTTP API (default http://localhost:11434, ai_base_url to override)
    "local"    ANY local OpenAI-compatible server: LM Studio (:1234), llama.cpp
               server (:8080), vLLM, text-generation-webui... default
               http://localhost:1234/v1, override with ai_base_url. No API key.
    "cli"      ANY local AI as a command line (ai_cli_command): prompt JSON on
               stdin, answer on stdout. No shell involved (shlex.split only).
    "mock"     deterministic, no network (tests/demos)

  CLOUD (data leaves the machine — the GUI shows a warning banner):
    "anthropic"  Claude / Anthropic (default https://api.anthropic.com) — needs ai_api_key
    "mistral"  Mistral AI (default https://api.mistral.ai/v1) — needs ai_api_key
    "openai"   OpenAI (default https://api.openai.com/v1)      — needs ai_api_key
    "openai_compat"  any custom OpenAI-compatible cloud endpoint
                     (set ai_base_url + ai_api_key)

API keys live ONLY in ~/.devdash/config.json (chmod 600), never in the
database, never hardcoded, never echoed in errors.
"""

from __future__ import annotations

import urllib.parse

from devdash.core import config as cfgmod
from .provider import AIProvider, AIError, AIDisabledError
from .providers.mock import MockProvider
from .providers.ollama import OllamaProvider
from .providers.openai_compat import OpenAICompatProvider
from .providers.anthropic import AnthropicProvider
from .providers.cli import CliProvider

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_LOCAL_OPENAI_URL = "http://localhost:1234/v1"  # LM Studio default; llama.cpp server is :8080
DEFAULT_MISTRAL_URL = "https://api.mistral.ai/v1"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com"

#: providers that never send data over the network by themselves
_LOCAL_PROVIDERS = ("ollama", "local", "cli", "mock")


def base_url_for(cfg: cfgmod.Config, provider: str) -> str:
    """Effective base URL for a provider (config override wins over preset)."""
    if provider == "ollama":
        return cfg.ai_base_url or DEFAULT_OLLAMA_URL
    if provider == "local":
        return cfg.ai_base_url or DEFAULT_LOCAL_OPENAI_URL
    if provider == "mistral":
        return cfg.ai_base_url or DEFAULT_MISTRAL_URL
    if provider == "anthropic":
        return cfg.ai_base_url or DEFAULT_ANTHROPIC_URL
    return cfg.ai_base_url or DEFAULT_OPENAI_URL  # openai | openai_compat


def _is_loopback(url: str) -> bool:
    """True if url points at this machine (a local server is not 'cloud')."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.startswith("127.")


def is_cloud(cfg: cfgmod.Config, provider: str | None = None) -> bool:
    """True when AI calls would send data to a remote server."""
    if not cfg.ai_enabled:
        return False
    provider = (provider or cfg.ai_provider or "ollama").lower()
    if provider in ("mock", "cli"):
        return False
    if provider == "local":
        return not _is_loopback(cfg.ai_base_url or DEFAULT_LOCAL_OPENAI_URL)
    if provider == "ollama":
        return not _is_loopback(cfg.ai_base_url or DEFAULT_OLLAMA_URL)
    # mistral / openai / openai_compat: cloud unless pointed at a loopback URL
    return not _is_loopback(base_url_for(cfg, provider))


def get_provider(cfg: cfgmod.Config) -> AIProvider | None:
    """None when the AI is disabled (the app is fully functional without it)."""
    if not cfg.ai_enabled:
        return None
    provider = (cfg.ai_provider or "ollama").lower()
    if provider == "mock":
        return MockProvider()
    if provider == "ollama":
        return OllamaProvider(cfg.ai_base_url or DEFAULT_OLLAMA_URL, cfg.ai_model)
    if provider == "cli":
        return CliProvider(cfg.ai_cli_command)
    if provider == "local":
        p = OpenAICompatProvider(base_url_for(cfg, "local"), "", cfg.ai_model,
                                 require_key=False)
        p.name = "local"
        return p
    if provider in ("mistral", "openai", "openai_compat"):
        p = OpenAICompatProvider(base_url_for(cfg, provider), cfg.ai_api_key, cfg.ai_model)
        p.name = provider
        return p
    if provider == "anthropic":
        return AnthropicProvider(base_url_for(cfg, "anthropic"), cfg.ai_api_key, cfg.ai_model)
    raise AIError(
        f"unknown ai_provider {cfg.ai_provider!r} in config.json — expected one of: "
        '"ollama", "local", "cli", "anthropic", "mistral", "openai", "openai_compat", "mock"'
    )


def ai_status(cfg: cfgmod.Config) -> dict:
    """Public status for the GUI/CLI — NEVER includes the API key."""
    enabled = bool(cfg.ai_enabled)
    provider = (cfg.ai_provider or "ollama").lower() if enabled else None
    return {
        "enabled": enabled,
        "provider": provider,
        "model": cfg.ai_model if enabled else None,
        # cloud = data leaves the machine (visual reminder required in the GUI)
        "cloud": is_cloud(cfg, provider),
        "flags": cfg.ai_feature_flags if enabled else {},
    }


# ---------------------------------------------------------------------------
# Provider catalog + GUI-driven configuration (no secrets ever leave this
# module through the catalog / status dicts).
# ---------------------------------------------------------------------------

#: Metadata for every selectable provider. `scope` drives the cloud warning;
#: the needs_* flags drive which form fields the GUI shows.
PROVIDER_CATALOG: list[dict] = [
    {"id": "ollama", "scope": "local", "needs_model": True, "needs_key": False,
     "needs_base_url": True, "needs_cli_command": False, "default_base_url": DEFAULT_OLLAMA_URL},
    {"id": "local", "scope": "local", "needs_model": True, "needs_key": False,
     "needs_base_url": True, "needs_cli_command": False, "default_base_url": DEFAULT_LOCAL_OPENAI_URL},
    {"id": "cli", "scope": "local", "needs_model": False, "needs_key": False,
     "needs_base_url": False, "needs_cli_command": True, "default_base_url": ""},
    {"id": "mock", "scope": "local", "needs_model": False, "needs_key": False,
     "needs_base_url": False, "needs_cli_command": False, "default_base_url": ""},
    {"id": "anthropic", "scope": "cloud", "needs_model": True, "needs_key": True,
     "needs_base_url": True, "needs_cli_command": False, "default_base_url": DEFAULT_ANTHROPIC_URL},
    {"id": "mistral", "scope": "cloud", "needs_model": True, "needs_key": True,
     "needs_base_url": True, "needs_cli_command": False, "default_base_url": DEFAULT_MISTRAL_URL},
    {"id": "openai", "scope": "cloud", "needs_model": True, "needs_key": True,
     "needs_base_url": True, "needs_cli_command": False, "default_base_url": DEFAULT_OPENAI_URL},
    {"id": "openai_compat", "scope": "cloud", "needs_model": True, "needs_key": True,
     "needs_base_url": True, "needs_cli_command": False, "default_base_url": ""},
]

PROVIDER_IDS = {p["id"] for p in PROVIDER_CATALOG}

#: AI feature flags that can be toggled individually
FEATURE_FLAGS = ("chat", "summarize", "plan", "feature_verdict")

#: bounds for validated string/int fields coming from the GUI form
_MAX_FIELD = 500
_CONTEXT_MIN, _CONTEXT_MAX = 500, 200_000


def provider_catalog() -> list[dict]:
    """Public provider metadata for the settings form (never contains keys)."""
    return [dict(p) for p in PROVIDER_CATALOG]


def apply_ai_settings(cfg: cfgmod.Config, data: dict) -> cfgmod.Config:
    """Validate + apply AI settings from a dict (GUI form / future CLI).

    Raises ValueError with a user-safe message (never echoes the API key).
    An empty/"***" ai_api_key means "keep the existing one".
    """
    if not isinstance(data, dict):
        raise ValueError("settings must be a JSON object")

    def _str_field(name: str, current: str) -> str:
        if name not in data:
            return current
        v = data[name]
        if v is None or v == "" or (name == "ai_api_key" and str(v).strip() in ("***", "••••••")):
            return current  # empty = keep existing (the key is write-only)
        if not isinstance(v, str) or len(v) > _MAX_FIELD:
            raise ValueError(f"{name}: expected a string of at most {_MAX_FIELD} characters")
        return v.strip()

    provider = _str_field("ai_provider", cfg.ai_provider).lower()
    if provider not in PROVIDER_IDS:
        raise ValueError(
            f"unknown ai_provider {provider!r} — expected one of: " + ", ".join(sorted(PROVIDER_IDS))
        )
    cfg.ai_provider = provider
    cfg.ai_model = _str_field("ai_model", cfg.ai_model)
    cfg.ai_api_key = _str_field("ai_api_key", cfg.ai_api_key)
    cfg.ai_cli_command = _str_field("ai_cli_command", cfg.ai_cli_command)

    base_url = _str_field("ai_base_url", cfg.ai_base_url)
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError("ai_base_url must start with http:// or https://")
    cfg.ai_base_url = base_url

    if "ai_enabled" in data:
        if not isinstance(data["ai_enabled"], bool):
            raise ValueError("ai_enabled must be true or false")
        cfg.ai_enabled = data["ai_enabled"]

    if "ai_context_max_chars" in data and data["ai_context_max_chars"] not in (None, ""):
        v = data["ai_context_max_chars"]
        if isinstance(v, bool) or not isinstance(v, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError("ai_context_max_chars must be an integer")
        if not (_CONTEXT_MIN <= v <= _CONTEXT_MAX):
            raise ValueError(f"ai_context_max_chars must be between {_CONTEXT_MIN} and {_CONTEXT_MAX}")
        cfg.ai_context_max_chars = v

    if "ai_feature_flags" in data and data["ai_feature_flags"] is not None:
        flags = data["ai_feature_flags"]
        if not isinstance(flags, dict):
            raise ValueError("ai_feature_flags must be an object")
        new_flags = dict(cfg.ai_feature_flags)
        for k, v in flags.items():
            if k not in FEATURE_FLAGS:
                continue  # unknown flag: ignore
            if not isinstance(v, bool):
                raise ValueError(f"ai_feature_flags.{k} must be true or false")
            new_flags[k] = v
        cfg.ai_feature_flags = new_flags

    return cfg


def public_ai_config(cfg: cfgmod.Config, home=None) -> dict:
    """Config view for the GUI — the API key is NEVER included, only whether one is set."""
    from pathlib import Path as _P
    return {
        "ai_enabled": bool(cfg.ai_enabled),
        "ai_provider": (cfg.ai_provider or "ollama").lower(),
        "ai_model": cfg.ai_model,
        "ai_base_url": cfg.ai_base_url,
        "ai_cli_command": cfg.ai_cli_command,
        "ai_context_max_chars": cfg.ai_context_max_chars,
        "ai_feature_flags": dict(cfg.ai_feature_flags),
        "api_key_set": bool(cfg.ai_api_key),
        "cloud": is_cloud(cfg),
        "config_path": str(cfgmod.config_path(_P(home) if home else None)),
    }
