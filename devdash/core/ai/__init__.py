"""Optional AI assistant layer (step i).

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
