"""Self-update helpers — update DevDash easily after a `git pull` (or another
install mode).

Install modes detected:
  - "git"  : the devdash package lives inside a git checkout (pip install -e .)
             -> update = `git pull --ff-only` + `pip install -e .` to refresh deps
  - "exe"  : PyInstaller standalone binary (sys.frozen)
             -> update = rebuild (scripts/build_exe.py) or replace the binary
  - "pip"  : installed from a package (pip / pipx), source repo unknown
             -> update = `pip install --upgrade devdash` / `pipx upgrade devdash`

Safety: pull is ALWAYS --ff-only, never force; a dirty working tree aborts the
update (the user commits themselves). Network happens ONLY on explicit user
actions: `git fetch` in check_for_updates(), `git pull` in update(). git_status()
is always offline.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_GIT_TIMEOUT = 60
_FETCH_TIMEOUT = 30
_PIP_TIMEOUT = 300


def package_root() -> Path:
    """Repo root when devdash is imported from a source checkout."""
    return Path(__file__).resolve().parent.parent.parent


def package_repo() -> Path | None:
    """The git repo containing the devdash package, if any."""
    root = package_root()
    return root if (root / ".git").exists() else None


def install_mode() -> str:
    if getattr(sys, "frozen", False):
        return "exe"
    return "git" if package_repo() is not None else "pip"


def _git(repo: Path, *args: str, timeout: int = _GIT_TIMEOUT) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def git_status(repo: Path | None = None) -> dict:
    """Local git info WITHOUT any network call (safe to call often)."""
    repo = repo or package_repo()
    out = {"mode": install_mode(), "git": False}
    if repo is None:
        return out
    rc, head = _git(repo, "rev-parse", "--short", "HEAD")
    if rc != 0:
        return out
    _, branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _, dirty = _git(repo, "status", "--porcelain")
    rc_remote, remote = _git(repo, "remote", "get-url", "origin")
    out.update({
        "git": True,
        "repo": str(repo),
        "head": head,
        "branch": branch,
        "dirty": bool(dirty),
        "remote": remote if rc_remote == 0 else None,  # its own rc, not HEAD's
    })
    return out


def check_for_updates(repo: Path | None = None, fetch: bool = True) -> dict:
    """Fetch origin (network, short timeout) and compare with the upstream.
    Returns {update_available, behind, ahead, local, remote} or {error}."""
    repo = repo or package_repo()
    if repo is None:
        return {"mode": install_mode(), "git": False,
                "note": "not a git checkout — update via pip/pipx or by replacing the executable"}
    if fetch:
        rc, out = _git(repo, "fetch", "origin", timeout=_FETCH_TIMEOUT)
        if rc != 0:
            return {"git": True, "error": f"git fetch failed: {out[:200]}"}
    rc, up = _git(repo, "rev-parse", "--verify", "@{u}")
    if rc != 0:
        return {"git": True, "error": "no upstream branch configured (git branch --set-upstream-to)"}
    _, counts = _git(repo, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    try:
        ahead, behind = (counts.split() + ["0", "0"])[:2]
        int(behind); int(ahead)  # validate before use
    except (ValueError, IndexError):
        return {"git": True, "error": f"unexpected git rev-list output: {counts[:120]!r}"}
    _, local = _git(repo, "rev-parse", "--short", "HEAD")
    _, remote = _git(repo, "rev-parse", "--short", "@{u}")
    return {
        "git": True, "mode": "git",
        "update_available": int(behind) > 0,
        "behind": int(behind), "ahead": int(ahead),
        "local": local, "remote": remote,
    }


def update(repo: Path | None = None, reinstall: bool = True) -> dict:
    """Apply the update. Git mode: ff-only pull (aborted when the tree is dirty)
    + `pip install -e .` to refresh dependencies. Other modes: guidance only.
    Always returns a structured dict; `restart_needed` is True on success."""
    repo = repo or package_repo()
    is_git = repo is not None and (repo / ".git").exists()
    mode = "exe" if getattr(sys, "frozen", False) else ("git" if is_git else "pip")
    if not is_git:
        if mode == "exe":
            return {"ok": False, "mode": mode, "restart_needed": False,
                    "error": "standalone executable: rebuild it (py -3 scripts/build_exe.py) "
                             "or replace the binary with the new release"}
        return {"ok": False, "mode": mode, "restart_needed": False,
                "error": "not a git checkout: update with `pip install --upgrade devdash` "
                         "(or `pipx upgrade devdash`)"}
    steps: list[str] = []
    st = git_status(repo)
    if st.get("dirty"):
        return {"ok": False, "mode": "git", "restart_needed": False,
                "error": "working tree has uncommitted changes — commit or stash them first, "
                         "then re-run `devdash update`"}
    rc, out = _git(repo, "pull", "--ff-only")
    if rc != 0:
        return {"ok": False, "mode": "git", "restart_needed": False,
                "error": f"git pull --ff-only failed: {out[:300]}"}
    steps.append("git pull --ff-only")
    if reinstall:
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "-e", str(repo)],
                capture_output=True, text=True, timeout=_PIP_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            r = subprocess.CompletedProcess([], 1, stderr=str(e))
        if r.returncode != 0:
            return {"ok": False, "mode": "git", "steps": steps, "restart_needed": False,
                    "error": f"code updated but dependency reinstall failed: {(r.stderr or '')[:300]}"}
        steps.append("pip install -e . (dependencies refreshed)")
    _, head = _git(repo, "rev-parse", "--short", "HEAD")
    return {"ok": True, "mode": "git", "steps": steps, "head": head,
            "restart_needed": True,
            "note": "restart the GUI / new CLI invocations pick up the new code automatically"}
