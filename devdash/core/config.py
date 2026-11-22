"""DevDash configuration: paths (DEVDASH_HOME) + config.json handling.

Configuration (AI keys, toggles, commit-parsing patterns, pomodoro defaults)
lives in <DEVDASH_HOME>/config.json -- deliberately SEPARATE from the project
data (devdash.db), so data backups never contain API keys.

DEVDASH_HOME defaults to ~/.devdash (Windows: %USERPROFILE%\\.devdash) and can
be overridden with the DEVDASH_HOME environment variable, e.g. to point at a
synced folder (OneDrive/Dropbox/USB) for portability between machines.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Regexes applied to git commit messages to link them to tasks/features.
# Capture group 1 = task id (patterns 1-2) or feature name (pattern 3).
DEFAULT_COMMIT_PATTERNS = [
    r"#(\d+)",                       # "fix dash #42"
    r"task[-_ ]?(\d+)",              # "task-42: ..."
    r"feat(?:ure)?\(([^)]+)\)",      # "feat(dash): ..."
]


@dataclass
class Config:
    # --- UI language: "fr" (default) or "en"; drives CLI and GUI ---
    lang: str = "fr"

    # --- AI assistant (fully optional; app works 100% without it) ---
    # ai_provider: "ollama" | "local" | "cli" | "anthropic" | "mistral" | "openai" | "openai_compat" | "mock"
    #   ollama         local HTTP API of Ollama (default http://localhost:11434)
    #   local          ANY local OpenAI-compatible server (LM Studio :1234, llama.cpp :8080, ...)
    #   cli            ANY local AI run as a command line (prompt JSON on stdin, answer on stdout)
    #   anthropic      Claude models via the Anthropic API (cloud, key required)
    #   mistral/openai cloud OpenAI-compatible APIs (default base URLs, key required)
    #   openai_compat  custom OpenAI-compatible endpoint (set ai_base_url + ai_api_key)
    #   mock           deterministic, no network (tests/demos)
    ai_enabled: bool = False
    ai_provider: str = "ollama"
    ai_model: str = ""               # e.g. "mistral-small-latest" or "qwen3:8b"
    ai_api_key: str = ""             # never hardcoded, never logged
    ai_base_url: str = ""            # e.g. "https://api.mistral.ai/v1" | "http://localhost:11434" | "http://localhost:1234/v1"
    ai_cli_command: str = ""         # for ai_provider="cli": e.g. "llama-cli -m model.gguf --no-display"
    ai_context_max_chars: int = 6000  # max size of the compact context sent to the model
    ai_feature_flags: dict = field(default_factory=lambda: {
        "chat": True, "summarize": True, "plan": True, "suggest": True,
        "feature_verdict": True, "task_verdict": True,
    })

    # --- git commit message parsing ---
    commit_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_COMMIT_PATTERNS))

    # --- pomodoro defaults ---
    pomodoro_focus_min: int = 25
    pomodoro_break_min: int = 5

    # --- git watcher (GUI polls repos for new commits) ---
    git_watch_interval_sec: int = 20

    # --- GUI ---
    gui_port: int = 8765
    theme: str = "dark"  # "dark" | "light"


def devdash_home() -> Path:
    """The single portable data folder. Created on first use."""
    env = os.environ.get("DEVDASH_HOME")
    home = Path(env).expanduser() if env else Path.home() / ".devdash"
    home.mkdir(parents=True, exist_ok=True)
    return home


def db_path(home: Path | None = None) -> Path:
    return (home or devdash_home()) / "devdash.db"


def config_path(home: Path | None = None) -> Path:
    return (home or devdash_home()) / "config.json"


# Expected types per config key, used to validate config.json on load.
# A value of the wrong type is ignored (default kept) instead of crashing.
_KEY_TYPES: dict[str, tuple[type, ...]] = {
    "lang": (str,),
    "ai_enabled": (bool,),
    "ai_provider": (str,),
    "ai_model": (str,),
    "ai_api_key": (str,),
    "ai_base_url": (str,),
    "ai_cli_command": (str,),
    "ai_context_max_chars": (int,),
    "ai_feature_flags": (dict,),
    "commit_patterns": (list,),
    "pomodoro_focus_min": (int,),
    "pomodoro_break_min": (int,),
    "git_watch_interval_sec": (int,),
    "gui_port": (int,),
    "theme": (str,),
}


def load_config(home: Path | None = None) -> Config:
    """Load config.json. Missing file, malformed JSON or wrong-typed values all
    fall back to defaults for the affected keys -- the app must never crash
    because of a hand-edited config file."""
    path = config_path(home)
    cfg = Config()
    if not path.exists():
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return cfg  # malformed file: keep defaults rather than crash
    if not isinstance(data, dict):
        return cfg
    for key, value in data.items():
        expected = _KEY_TYPES.get(key)
        if expected is None or not hasattr(cfg, key):
            continue  # unknown key: ignore
        # bool is a subclass of int: reject bools where a real int is expected
        if isinstance(value, bool) and bool not in expected:
            continue
        if not isinstance(value, expected):
            continue
        if key == "commit_patterns" and not all(isinstance(p, str) for p in value):
            continue
        setattr(cfg, key, value)
    return cfg


def save_config(cfg: Config, home: Path | None = None) -> Path:
    """Persist config.json (chmod 600 where supported: it may hold an API key)."""
    path = config_path(home)
    path.write_text(
        json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows: best effort
    return path


# ---------------------------------------------------------------------------
# General (non-AI) settings editable from the GUI settings tab.
# ---------------------------------------------------------------------------

#: bounds for the numeric general settings
_INT_BOUNDS = {
    "pomodoro_focus_min": (1, 240),
    "pomodoro_break_min": (1, 120),
    "git_watch_interval_sec": (5, 3600),
    "gui_port": (1024, 65535),
}
_MAX_PATTERNS = 10
_MAX_PATTERN_LEN = 200

# A quantifier applied to a group that already contains a quantifier
# (e.g. "(a+)+", "(a|b)*{2,}", "(x{2,3})+") is the classic catastrophic-backtracking
# shape. Rejecting them at validation time keeps `gitwatch.parse_message` safe
# without needing a regex timeout (not available on Windows).
_NESTED_QUANT_RE = re.compile(r"\([^()]*[+*{][^()]*\)[+*{]")


def _has_nested_quantifier(pattern: str) -> bool:
    """Heuristic: True if the pattern looks like it can backtrack exponentially."""
    return bool(_NESTED_QUANT_RE.search(pattern))


def apply_general_settings(cfg: Config, data: dict, allowed_langs=("fr", "en")) -> Config:
    """Validate + apply general settings from a dict (GUI settings tab).

    Raises ValueError with a user-safe message on invalid input.
    `lang` must be one of `allowed_langs`.
    """
    if not isinstance(data, dict):
        raise ValueError("settings must be a JSON object")

    if "lang" in data and data["lang"] not in (None, ""):
        lang = data["lang"]
        if not isinstance(lang, str) or lang not in allowed_langs:
            raise ValueError(f"lang must be one of: {', '.join(allowed_langs)}")
        cfg.lang = lang

    if "theme" in data and data["theme"] not in (None, ""):
        theme = data["theme"]
        if theme not in ("dark", "light"):
            raise ValueError("theme must be 'dark' or 'light'")
        cfg.theme = theme

    for key, (lo, hi) in _INT_BOUNDS.items():
        if key not in data or data[key] in (None, ""):
            continue
        v = data[key]
        if isinstance(v, bool) or not isinstance(v, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be an integer")
        if not (lo <= v <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}")
        setattr(cfg, key, v)

    if "commit_patterns" in data and data["commit_patterns"] is not None:
        patterns = data["commit_patterns"]
        if isinstance(patterns, str):
            patterns = [p for p in patterns.splitlines() if p.strip()]
        if not isinstance(patterns, list) or not patterns:
            raise ValueError("commit_patterns must be a non-empty list of regex strings")
        if len(patterns) > _MAX_PATTERNS:
            raise ValueError(f"commit_patterns: at most {_MAX_PATTERNS} patterns")
        for p in patterns:
            if not isinstance(p, str) or len(p) > _MAX_PATTERN_LEN:
                raise ValueError(f"each commit pattern must be a string of at most {_MAX_PATTERN_LEN} chars")
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"invalid commit regex {p!r}: {e}")
            if _has_nested_quantifier(p):
                raise ValueError(
                    f"commit pattern {p!r} is rejected: nested quantifiers can cause "
                    "catastrophic backtracking (ReDoS)"
                )
        cfg.commit_patterns = list(patterns)

    return cfg
