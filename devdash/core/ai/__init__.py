"""Optional AI assistant layer (step i).

Fully optional: with `ai_enabled = false` (default) in config.json,
`get_provider()` returns None and every AI feature cleanly reports disabled.
Providers are interchangeable: "ollama" (local, private), "openai_compat"
(Mistral, OpenAI, or any OpenAI-compatible API), and "mock" (deterministic,
for tests/demos). API keys live ONLY in ~/.devdash/config.json, never in the
database, never hardcoded.
"""

from __future__ import annotations

from devdash.core import config as cfgmod
from .provider import AIProvider, AIError, AIDisabledError
from .providers.mock import MockProvider
from .providers.ollama import OllamaProvider
from .providers.openai_compat import OpenAICompatProvider

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MISTRAL_URL = "https://api.mistral.ai/v1"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"


def get_provider(cfg: cfgmod.Config) -> AIProvider | None:
    """None when the AI is disabled (the app is fully functional without it)."""
    if not cfg.ai_enabled:
        return None
    provider = (cfg.ai_provider or "ollama").lower()
    if provider == "mock":
        return MockProvider()
    if provider == "ollama":
        return OllamaProvider(cfg.ai_base_url or DEFAULT_OLLAMA_URL, cfg.ai_model)
    # "mistral" | "openai" | "openai_compat" -> OpenAI-compatible HTTP API
    base = cfg.ai_base_url or (DEFAULT_MISTRAL_URL if provider == "mistral" else DEFAULT_OPENAI_URL)
    return OpenAICompatProvider(base, cfg.ai_api_key, cfg.ai_model)


def ai_status(cfg: cfgmod.Config) -> dict:
    """Public status for the GUI/CLI (no secrets)."""
    enabled = bool(cfg.ai_enabled)
    provider = (cfg.ai_provider or "ollama").lower() if enabled else None
    return {
        "enabled": enabled,
        "provider": provider,
        "model": cfg.ai_model if enabled else None,
        # cloud = data leaves the machine (visual reminder required in the GUI)
        "cloud": enabled and provider not in ("ollama", "mock", None),
        "flags": cfg.ai_feature_flags if enabled else {},
    }
