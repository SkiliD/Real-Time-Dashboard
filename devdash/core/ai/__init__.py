"""optional AI layer -- stays dark unless ai_enabled=true

Small on purpose -- it has to stay fast.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse

from devdash.core import config as cfgmod
from devdash.core import i18n
from .provider import AIProvider, AIError

#: provider key -> (module, class), imported LAZILY: importing devdash.cli.main
#: (every CLI call, even --help) must not pay for urllib/http.client through the
#: cloud provider modules
_PROVIDER_CLASSES = {
    "mock": (".providers.mock", "MockProvider"),
    "ollama": (".providers.ollama", "OllamaProvider"),
    "openai_compat": (".providers.openai_compat", "OpenAICompatProvider"),
    "anthropic": (".providers.anthropic", "AnthropicProvider"),
    "cli": (".providers.cli", "CliProvider"),
}


def _provider_cls(key: str):
    import importlib
    mod_name, cls_name = _PROVIDER_CLASSES[key]
    return getattr(importlib.import_module(mod_name, package=__package__), cls_name)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_LOCAL_OPENAI_URL = "http://localhost:1234/v1"  # LM Studio default; llama.cpp server is :8080
DEFAULT_MISTRAL_URL = "https://api.mistral.ai/v1"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com"
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1"  # one key -> every major cloud model



DEFAULT_BASE_URLS = {
    "ollama": DEFAULT_OLLAMA_URL,
    "local": DEFAULT_LOCAL_OPENAI_URL,
    "mistral": DEFAULT_MISTRAL_URL,
    "anthropic": DEFAULT_ANTHROPIC_URL,
    "openrouter": DEFAULT_OPENROUTER_URL,
    "openai": DEFAULT_OPENAI_URL,
    "openai_compat": DEFAULT_OPENAI_URL,
}


def base_url_for(cfg: cfgmod.Config, provider: str) -> str:
    """Effective base URL for a provider (config override wins over preset)."""
    return cfg.ai_base_url or DEFAULT_BASE_URLS.get(provider, DEFAULT_OPENAI_URL)


def _is_loopback(url: str) -> bool:
    """True if url points at this machine (a local server is not 'cloud').
    The hostname is RESOLVED, not string-matched: '127.0.0.1.evil.com' (wildcard
    DNS) must NOT count as local. Unresolvable hosts fail closed (treated as
    remote)."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if host in ("localhost", "::1", "0.0.0.0"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # unresolvable: fail closed, treat as remote
    return all(ipaddress.ip_address(info[4][0]).is_loopback for info in infos)


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
        return _provider_cls("mock")()
    if provider == "ollama":
        return _provider_cls("ollama")(cfg.ai_base_url or DEFAULT_OLLAMA_URL, cfg.ai_model)
    if provider == "cli":
        return _provider_cls("cli")(cfg.ai_cli_command)
    if provider == "local":
        p = _provider_cls("openai_compat")(base_url_for(cfg, "local"), "", cfg.ai_model,
                                            require_key=False)
        p.name = "local"
        return p
    if provider in ("mistral", "openai", "openai_compat", "openrouter"):
        p = _provider_cls("openai_compat")(base_url_for(cfg, provider), cfg.ai_api_key, cfg.ai_model)
        p.name = provider
        return p
    if provider == "anthropic":
        return _provider_cls("anthropic")(base_url_for(cfg, "anthropic"), cfg.ai_api_key, cfg.ai_model)
    raise AIError(
        f"unknown ai_provider {cfg.ai_provider!r} in config.json — expected one of: "
        '"ollama", "local", "cli", "anthropic", "mistral", "openai", "openai_compat", '
        '"openrouter", "mock"'
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
    {"id": "openrouter", "scope": "cloud", "needs_model": True, "needs_key": True,
     "needs_base_url": True, "needs_cli_command": False, "default_base_url": DEFAULT_OPENROUTER_URL},
]

#: Curated model presets per provider ({id, label}). Empty list = free-text model
#: field (local servers pull their models dynamically, presets would be stale).
#: OpenRouter is the gateway to every major cloud model with a single key.
PROVIDER_MODELS: dict[str, list[dict]] = {
    "mistral": [
        {"id": "mistral-large-latest", "label": "Mistral Large (mistral-large-latest)"},
        {"id": "magistral-large-latest", "label": "Magistral Large — raisonnement (magistral-large-latest)"},
        {"id": "codestral-latest", "label": "Codestral — code (codestral-latest)"},
        {"id": "mistral-small-latest", "label": "Mistral Small — rapide (mistral-small-latest)"},
    ],
    "openai": [
        {"id": "gpt-5", "label": "GPT-5 (gpt-5)"},
        {"id": "gpt-5-mini", "label": "GPT-5 mini (gpt-5-mini)"},
        {"id": "gpt-4.1", "label": "GPT-4.1 (gpt-4.1)"},
        {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini (gpt-4.1-mini)"},
        {"id": "o4-mini", "label": "o4-mini — raisonnement (o4-mini)"},
    ],
    "anthropic": [
        {"id": "claude-opus-4-5", "label": "Claude Opus 4.5 (claude-opus-4-5)"},
        {"id": "claude-sonnet-4-5", "label": "Claude Sonnet 4.5 (claude-sonnet-4-5)"},
        {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5 — rapide (claude-haiku-4-5)"},
    ],
    "openrouter": [
        {"id": "anthropic/claude-opus-4.5", "label": "Claude Opus 4.5 (Anthropic)"},
        {"id": "anthropic/claude-sonnet-4.5", "label": "Claude Sonnet 4.5 (Anthropic)"},
        {"id": "openai/gpt-5", "label": "GPT-5 (OpenAI)"},
        {"id": "openai/gpt-5-codex", "label": "GPT-5 Codex (OpenAI)"},
        {"id": "google/gemini-2.5-pro", "label": "Gemini 2.5 Pro (Google)"},
        {"id": "deepseek/deepseek-chat", "label": "DeepSeek Chat (DeepSeek)"},
        {"id": "qwen/qwen3-coder", "label": "Qwen3 Coder (Qwen)"},
        {"id": "mistralai/mistral-large-latest", "label": "Mistral Large (Mistral)"},
    ],
    "ollama": [],
    "local": [],
    "cli": [],
    "mock": [],
    "openai_compat": [],
}

PROVIDER_IDS = {p["id"] for p in PROVIDER_CATALOG}

#: AI feature flags that can be toggled individually (must match Config.ai_feature_flags keys)
FEATURE_FLAGS = ("chat", "summarize", "plan", "suggest", "feature_verdict", "task_verdict")

#: bounds for validated string/int fields coming from the GUI form
_MAX_FIELD = 500
_CONTEXT_MIN, _CONTEXT_MAX = 500, 200_000


def provider_catalog() -> list[dict]:
    """Public provider metadata for the settings form (never contains keys).
    Each entry also carries `models`: the curated preset list for the model
    dropdown (empty = free-text field, e.g. local servers)."""
    return [{**p, "models": [dict(m) for m in PROVIDER_MODELS.get(p["id"], [])]}
            for p in PROVIDER_CATALOG]


def apply_ai_settings(cfg: cfgmod.Config, data: dict) -> cfgmod.Config:
    """Validate + apply AI settings from a dict (GUI form / future CLI).

    Raises ValueError with a user-safe message (never echoes the API key).
    An empty/"***" ai_api_key means "keep the existing one".
    """
    if not isinstance(data, dict):
        raise ValueError(i18n.tr("err.settings_not_object"))

    def _str_field(name: str, current: str) -> str:
        if name not in data:
            return current
        v = data[name]
        if v is None or v == "" or (name == "ai_api_key" and str(v).strip() in ("***", "••••••")):
            return current  # empty = keep existing (the key is write-only)
        if not isinstance(v, str) or len(v) > _MAX_FIELD:
            raise ValueError(i18n.tr("err.ai_field_len", name=name, max=_MAX_FIELD))
        return v.strip()

    provider = _str_field("ai_provider", cfg.ai_provider).lower()
    if provider not in PROVIDER_IDS:
        raise ValueError(
            i18n.tr("err.ai_provider_unknown", provider=provider, items=", ".join(sorted(PROVIDER_IDS)))
        )
    cfg.ai_provider = provider
    cfg.ai_model = _str_field("ai_model", cfg.ai_model)
    cfg.ai_api_key = _str_field("ai_api_key", cfg.ai_api_key)
    cfg.ai_cli_command = _str_field("ai_cli_command", cfg.ai_cli_command)

    base_url = _str_field("ai_base_url", cfg.ai_base_url)
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError(i18n.tr("err.ai_url_scheme"))
    cfg.ai_base_url = base_url

    if "ai_enabled" in data:
        if not isinstance(data["ai_enabled"], bool):
            raise ValueError(i18n.tr("err.ai_enabled_bool"))
        cfg.ai_enabled = data["ai_enabled"]

    if "ai_context_max_chars" in data and data["ai_context_max_chars"] not in (None, ""):
        v = data["ai_context_max_chars"]
        if isinstance(v, bool) or not isinstance(v, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(i18n.tr("err.ai_ctx_chars_int"))
        if not (_CONTEXT_MIN <= v <= _CONTEXT_MAX):
            raise ValueError(i18n.tr("err.ai_ctx_range", min=_CONTEXT_MIN, max=_CONTEXT_MAX))
        cfg.ai_context_max_chars = v

    if "ai_feature_flags" in data and data["ai_feature_flags"] is not None:
        flags = data["ai_feature_flags"]
        if not isinstance(flags, dict):
            raise ValueError(i18n.tr("err.ai_flags_object"))
        new_flags = dict(cfg.ai_feature_flags)
        for k, v in flags.items():
            if k not in FEATURE_FLAGS:
                continue  # unknown flag: ignore
            if not isinstance(v, bool):
                raise ValueError(i18n.tr("err.ai_flag_bool", flag=k))
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
