"""attach an existing folder on disk as a project"""

from __future__ import annotations

from pathlib import Path

#: marker files/patterns -> engine id (checked in order)
_ENGINE_MARKERS: tuple[tuple[str, str], ...] = (
    ("ProjectSettings/ProjectVersion.txt", "unity"),   # Unity
    ("project.godot", "godot"),                        # Godot
    ("package.json", "web"),                           # web (npm)
)


def find_git_root(path: Path) -> Path | None:
    """Walk up from `path` looking for a `.git` entry (dir, or file for
    worktrees/submodules). Returns the repo root, or None."""
    try:
        p = path.expanduser().resolve()
    except OSError:
        return None
    if p.is_file():
        p = p.parent
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def detect_engine(path: Path) -> str:
    """Best-effort engine detection from marker files; 'other' when unknown."""
    try:
        p = path.expanduser().resolve()
    except OSError:
        return "other"
    if not p.is_dir():
        return "other"
    for marker, engine in _ENGINE_MARKERS:
        if (p / marker).exists():
            return engine
    try:
        if any(p.glob("*.uproject")):  # Unreal
            return "unreal"
    except OSError:
        pass
    return "other"


def suggest_name(path: Path) -> str:
    """Folder name as a human project name (underscores/dashes -> spaces)."""
    try:
        p = path.expanduser().resolve()
    except OSError:
        return ""
    name = p.name or str(p)
    for sep in ("_", "-"):
        name = name.replace(sep, " ")
    name = " ".join(name.split()).strip()
    if not name:
        return str(p)
    return name[0].upper() + name[1:]  # keep CamelCase folder names as-is


def detect_project(path: str | Path) -> dict:
    """Full probe of a project folder. Never raises on bad input: returns
    exists/is_dir flags so callers can 400 cleanly."""
    raw = str(path or "").strip()
    out = {"path": raw, "exists": False, "is_dir": False,
           "name": "", "engine": "other", "git_root": None}
    if not raw or len(raw) > 4096:
        return out
    try:
        p = Path(raw).expanduser()
        out["exists"] = p.exists()
        out["is_dir"] = p.is_dir()
        if out["is_dir"]:
            out["name"] = suggest_name(p)
            out["engine"] = detect_engine(p)
            root = find_git_root(p)
            out["git_root"] = str(root) if root else None
        out["path"] = str(p.resolve()) if out["exists"] else raw
    except OSError:
        pass
    return out
