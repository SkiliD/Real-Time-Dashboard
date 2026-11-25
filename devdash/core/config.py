# DEVDASH_HOME paths + config.json (tolerant loader)
# Read-only on purpose.

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
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
    # ai_provider: "ollama" | "local" | "cli" | "anthropic" | "mistral" | "openai" | "openai_compat" | "openrouter" | "mock"
    #   ollama         local HTTP API of Ollama (default http://localhost:11434)
    #   local          ANY local OpenAI-compatible server (LM Studio :1234, llama.cpp :8080, ...)
    #   cli            ANY local AI run as a command line (prompt JSON on stdin, answer on stdout)
    #   anthropic      Claude models via the Anthropic API (cloud, key required)
    #   mistral/openai cloud OpenAI-compatible APIs (default base URLs, key required)
    #   openai_compat  custom OpenAI-compatible endpoint (set ai_base_url + ai_api_key)
    #   openrouter     ONE key for every major cloud model (Claude, GPT, Gemini, ...) — pick the model in the GUI preset list
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

    # --- automatic backups (GUI start) ---
    auto_backup_days: int = 1     # 0 = disabled; a zip is written at most every N days
    auto_backup_keep: int = 5     # zips kept in <home>/backups
    last_auto_backup: str = ""    # ISO timestamp of the last automatic backup
    theme: str = "auto"  # "dark" | "light" | "auto" (follows the OS)
    gui_window: dict = field(default_factory=dict)  # {"x","y","w","h","maximized"}: geometry saved at window close


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
    "auto_backup_days": (int,),
    "auto_backup_keep": (int,),
    "last_auto_backup": (str,),
    "theme": (str,),
    "gui_window": (dict,),
}


# mtime/size cache so the polled endpoints (e.g. /api/ai/status every 1.5 s) do not
# re-read + re-parse config.json from disk on every call; invalidated by save_config
_load_cache: dict = {"key": None, "mtime": None, "size": None, "cfg": None}


def load_config(home: Path | None = None) -> Config:
    """Load config.json. Missing file, malformed JSON or wrong-typed values all
    fall back to defaults for the affected keys -- the app must never crash
    because of a hand-edited config file. Results are cached by (path, mtime,
    size): the GUI polls endpoints that call this every 1.5 s."""
    path = config_path(home)
    cfg = Config()
    try:
        st = path.stat()
        ckey = (str(path), st.st_mtime_ns, st.st_size)
        if _load_cache["key"] == ckey and _load_cache["cfg"] is not None:
            return _load_cache["cfg"]
    except OSError:
        return cfg  # missing file
    if not path.exists():
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        _load_cache.update(key=ckey, mtime=st.st_mtime_ns, size=st.st_size, cfg=cfg)
        return cfg  # malformed file: keep defaults rather than crash
    if not isinstance(data, dict):
        _load_cache.update(key=ckey, mtime=st.st_mtime_ns, size=st.st_size, cfg=cfg)
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
    _load_cache.update(key=ckey, mtime=st.st_mtime_ns, size=st.st_size, cfg=cfg)
    return cfg


#: serializes load-modify-save cycles: the threaded GUI server can save settings
#: from several request threads at once — without a lock, last-writer-wins loses
#: updates (and a crash mid-write used to corrupt the file, silently losing keys)
_SAVE_LOCK = threading.RLock()


def config_lock() -> threading.RLock:
    """Lock guarding load->modify->save cycles on config.json (RLock: save_config
    takes it too, so update_config can hold it across the whole cycle)."""
    return _SAVE_LOCK


def save_config(cfg: Config, home: Path | None = None) -> Path:
    """Persist config.json ATOMICALLY (tmp file + os.replace: a crash can never
    leave a half-written file) and chmod 600 where supported (may hold an API key)."""
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _SAVE_LOCK:
        fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(asdict(cfg), indent=2, ensure_ascii=False))
            os.replace(tmp, path)  # atomic on POSIX and Windows
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        from .db import restrict_permissions
        restrict_permissions(path)  # chmod 600 + icacls ACL reset on Windows
        # invalidate the load cache so the next load_config sees the new file
        try:
            st = path.stat()
            _load_cache.update(key=(str(path), st.st_mtime_ns, st.st_size), cfg=cfg)
        except OSError:
            _load_cache["key"] = None
    return path


def update_config(home: Path | None, fn) -> Config:
    """Atomic load -> modify -> save under the config lock (thread-safe for the
    GUI server). `fn(cfg)` mutates the loaded config; ValueError propagates and
    nothing is written. Returns the saved config."""
    with _SAVE_LOCK:
        cfg = load_config(home)
        fn(cfg)
        save_config(cfg, home)
        return cfg


# ---------------------------------------------------------------------------
# General (non-AI) settings editable from the GUI settings tab.
# ---------------------------------------------------------------------------

#: bounds for the numeric general settings
_INT_BOUNDS = {
    "pomodoro_focus_min": (1, 240),
    "pomodoro_break_min": (1, 120),
    "git_watch_interval_sec": (5, 3600),
    "gui_port": (1024, 65535),
    "auto_backup_days": (0, 365),
    "auto_backup_keep": (1, 100),
}
_MAX_PATTERNS = 10
_MAX_PATTERN_LEN = 200

# A quantifier applied to a group that already contains a quantifier
# (e.g. "(a+)+", "(a|b)*{2,}", "(x{2,3})+") is the classic catastrophic-backtracking
# shape. Rejecting them at validation time keeps `gitwatch.parse_message` safe
# without needing a regex timeout (not available on Windows).
_NESTED_QUANT_RE = re.compile(r"\([^()]*[+*{][^()]*\)[+*{]")
# alternation inside a quantified group, e.g. (a|a)+ or (a|a?)+ — also exponential
_ALT_QUANT_RE = re.compile(r"\([^()]*\|[^()]*\)[+*{]")


def _has_nested_quantifier(pattern: str) -> bool:
    """Heuristic: True if the pattern looks like it can backtrack exponentially."""
    return bool(_NESTED_QUANT_RE.search(pattern) or _ALT_QUANT_RE.search(pattern))


def apply_general_settings(cfg: Config, data: dict, allowed_langs=("fr", "en")) -> Config:
    """Validate + apply general settings from a dict (GUI settings tab).

    Raises ValueError with a user-safe message on invalid input.
    `lang` must be one of `allowed_langs`.
    """
    from .i18n import tr  # lazy import: i18n imports config (circular at module level)

    if not isinstance(data, dict):
        raise ValueError(tr("err.settings_not_object"))

    if "lang" in data and data["lang"] not in (None, ""):
        lang = data["lang"]
        if not isinstance(lang, str) or lang not in allowed_langs:
            raise ValueError(tr("err.lang_invalid", items=", ".join(allowed_langs)))
        cfg.lang = lang

    if "theme" in data and data["theme"] not in (None, ""):
        theme = data["theme"]
        if theme not in ("dark", "light", "auto"):
            raise ValueError(tr("err.theme_invalid"))
        cfg.theme = theme

    for key, (lo, hi) in _INT_BOUNDS.items():
        if key not in data or data[key] in (None, ""):
            continue
        v = data[key]
        if isinstance(v, bool) or not isinstance(v, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(tr("err.int_field", field=key))
        if not (lo <= v <= hi):
            raise ValueError(tr("err.int_range", field=key, lo=lo, hi=hi))
        setattr(cfg, key, v)

    if "commit_patterns" in data and data["commit_patterns"] is not None:
        patterns = data["commit_patterns"]
        if isinstance(patterns, str):
            patterns = [p for p in patterns.splitlines() if p.strip()]
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(tr("err.commit_patterns_invalid"))
        if len(patterns) > _MAX_PATTERNS:
            raise ValueError(tr("err.patterns_count", max=_MAX_PATTERNS))
        for p in patterns:
            if not isinstance(p, str) or len(p) > _MAX_PATTERN_LEN:
                raise ValueError(tr("err.pattern_len", max=_MAX_PATTERN_LEN))
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(tr("err.pattern_bad_regex", pattern=p, err=e))
            if _has_nested_quantifier(p):
                raise ValueError(tr("err.pattern_redos", pattern=p))
        cfg.commit_patterns = list(patterns)

    return cfg
