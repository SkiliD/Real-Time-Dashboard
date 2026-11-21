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
    ai_enabled: bool = False
    ai_provider: str = "ollama"      # "ollama" | "openai_compat" (Mistral/OpenAI/...)
    ai_model: str = ""               # e.g. "mistral-small-latest" or "qwen3:8b"
    ai_api_key: str = ""             # never hardcoded, never logged
    ai_base_url: str = ""            # e.g. "https://api.mistral.ai/v1" | "http://localhost:11434/v1"
    ai_feature_flags: dict = field(default_factory=lambda: {
        "chat": True, "summarize": True, "plan": True, "feature_verdict": True,
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


def load_config(home: Path | None = None) -> Config:
    """Load config.json; unknown keys are ignored, missing file = defaults."""
    path = config_path(home)
    cfg = Config()
    if not path.exists():
        return cfg
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, value in data.items():
        if hasattr(cfg, key):
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
