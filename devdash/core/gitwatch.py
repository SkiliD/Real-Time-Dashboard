"""Read-only Git integration (core).

DevDash NEVER writes to the user's repository: only read-only git commands
(`rev-parse`, `log`) are run via subprocess. Commits are cached in the local
SQLite DB (git_commits) and linked to tasks/features by parsing the commit
subject with configurable regexes (config.commit_patterns, default:
"#42" -> task 42, "task-42: ..." -> task 42, "feat(name): ..." -> feature).
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

from . import config, events, projects
from .models import GitCommit, now_iso

MAX_COMMITS_PER_SYNC = 1000


class GitError(Exception):
    """Raised when a git operation fails (not a repo, git missing, ...)."""


def _git(repo_path, *args: str, timeout: int = 30) -> str:
    """Run a read-only git command in repo_path and return stdout."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as e:
        raise GitError("git executable not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {' '.join(args)} timed out") from e
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def is_repo(path) -> bool:
    try:
        return _git(path, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False


# --- links (project <-> local repo) ----------------------------------------

def link(conn: sqlite3.Connection, project_id: int, repo_path: str) -> str:
    if projects.get(conn, project_id) is None:
        raise KeyError(f"no project with id {project_id}")
    p = Path(repo_path).expanduser().resolve()
    if not is_repo(p):
        raise GitError(f"not a git repository: {p}")
    conn.execute(
        "INSERT INTO git_links(project_id, repo_path, added_at) VALUES (?, ?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET repo_path = excluded.repo_path",
        (project_id, str(p), now_iso()),
    )
    conn.commit()
    events.append(conn, "git", project_id, "linked", {"repo_path": str(p)})
    return str(p)


def unlink(conn: sqlite3.Connection, project_id: int) -> None:
    conn.execute("DELETE FROM git_links WHERE project_id = ?", (project_id,))
    conn.commit()
    events.append(conn, "git", project_id, "unlinked", {})


def get_link(conn: sqlite3.Connection, project_id: int) -> str | None:
    row = conn.execute(
        "SELECT repo_path FROM git_links WHERE project_id = ?", (project_id,)
    ).fetchone()
    return row["repo_path"] if row else None


def linked_repos(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    rows = conn.execute("SELECT project_id, repo_path FROM git_links").fetchall()
    return [(r["project_id"], r["repo_path"]) for r in rows]


# --- commit parsing ----------------------------------------------------------

def parse_message(message: str, patterns: list[str]) -> tuple[int | None, str | None]:
    """Return (task_id, feature) parsed from a commit message, or (None, None).

    First matching pattern wins. A numeric capture group -> task id; otherwise
    the capture is treated as a feature name.
    """
    for pattern in patterns:
        m = re.search(pattern, message, re.IGNORECASE)
        if m and m.groups():
            value = m.group(1)
            if value.isdigit():
                return int(value), None
            return None, value
    return None, None


# --- sync (read-only) --------------------------------------------------------

def sync_repo(conn: sqlite3.Connection, repo_path: str, patterns: list[str]) -> dict:
    """Read `git log` of repo_path and cache commits in git_commits.

    Emits one `git.commit` event per NEW commit. Idempotent (INSERT OR IGNORE).
    """
    p = Path(repo_path).expanduser().resolve()
    if not p.exists():
        raise GitError(f"path does not exist: {p}")
    _git(p, "rev-parse", "--git-dir")  # raises GitError if not a repo
    branch = _git(p, "rev-parse", "--abbrev-ref", "HEAD").strip() or "HEAD"
    raw = _git(
        p, "log", "--format=%H%x1f%h%x1f%an%x1f%s%x1f%cI%x1e", "-n", str(MAX_COMMITS_PER_SYNC)
    )
    new = 0
    total = 0
    last_short = None
    for record in raw.split("\x1e"):
        record = record.strip("\r\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) != 5:
            continue
        commit_hash, short, author, subject, committed_at = parts
        total += 1
        last_short = short
        task_id, feature = parse_message(subject, patterns)
        cur = conn.execute(
            "INSERT OR IGNORE INTO git_commits(hash, repo_path, short, author, message, "
            "branch, committed_at, parsed_task_id, parsed_feature) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (commit_hash, str(p), short, author, subject, branch,
             committed_at, task_id, feature),
        )
        if cur.rowcount:  # new commit
            new += 1
            events.append(conn, "git", commit_hash, "commit", {
                "hash": commit_hash, "short": short, "message": subject,
                "author": author, "branch": branch, "committed_at": committed_at,
                "task_id": task_id, "feature": feature, "repo_path": str(p),
            })
    conn.execute(
        "INSERT INTO git_sync_state(repo_path, last_sync_at, last_seen_hash) "
        "VALUES (?, ?, ?) ON CONFLICT(repo_path) DO UPDATE SET "
        "last_sync_at = excluded.last_sync_at, last_seen_hash = excluded.last_seen_hash",
        (str(p), now_iso(), last_short),
    )
    conn.commit()
    return {"repo_path": str(p), "branch": branch, "new": new, "total": total}


def sync_all(conn: sqlite3.Connection, home=None) -> dict:
    """Sync every linked repo. Never raises: per-repo errors are collected."""
    cfg = config.load_config(home)
    patterns = cfg.commit_patterns
    results, errors = [], []
    for project_id, repo_path in linked_repos(conn):
        try:
            results.append(sync_repo(conn, repo_path, patterns))
        except GitError as e:
            errors.append({"project_id": project_id, "repo_path": repo_path, "error": str(e)})
    return {"repos": results, "errors": errors}


# --- queries -----------------------------------------------------------------

def commits(
    conn: sqlite3.Connection,
    repo_path: str | None = None,
    project_id: int | None = None,
    task_id: int | None = None,
    unparsed: bool = False,
    limit: int = 100,
) -> list[GitCommit]:
    clauses, values = [], []
    if repo_path is not None:
        clauses.append("repo_path = ?")
        values.append(str(Path(repo_path).resolve()))
    if project_id is not None:
        clauses.append("repo_path IN (SELECT repo_path FROM git_links WHERE project_id = ?)")
        values.append(project_id)
    if task_id is not None:
        clauses.append("parsed_task_id = ?")
        values.append(task_id)
    if unparsed:
        clauses.append("parsed_task_id IS NULL AND parsed_feature IS NULL")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM git_commits {where} ORDER BY committed_at DESC, hash DESC LIMIT ?",
        (*values, limit),
    ).fetchall()
    return [GitCommit.from_row(r) for r in rows]


def commits_for_task(conn: sqlite3.Connection, task_id: int) -> list[GitCommit]:
    return commits(conn, task_id=task_id, limit=500)


def unparsed_commits(conn: sqlite3.Connection, repo_path: str | None = None) -> list[GitCommit]:
    return commits(conn, repo_path=repo_path, unparsed=True, limit=500)


def status(conn: sqlite3.Connection, project_id: int) -> dict:
    """Git snapshot for a project: link, branch, last commit, today's count."""
    repo = get_link(conn, project_id)
    if repo is None:
        return {"project_id": project_id, "linked": False}
    branch = None
    last = None
    today_count = 0
    sync_state = None
    try:
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() or "HEAD"
        all_commits = commits(conn, repo_path=repo, limit=10000)
        if all_commits:
            last = all_commits[0].to_dict()
        from datetime import datetime, timezone
        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        today_count = sum(1 for c in all_commits if c.committed_at >= cutoff)
    except GitError as e:
        return {"project_id": project_id, "linked": True, "repo_path": repo, "error": str(e)}
    row = conn.execute(
        "SELECT last_sync_at, last_seen_hash FROM git_sync_state WHERE repo_path = ?", (repo,)
    ).fetchone()
    if row:
        sync_state = dict(row)
    return {
        "project_id": project_id, "linked": True, "repo_path": repo,
        "branch": branch, "last_commit": last, "commits_today": today_count,
        "last_sync_at": sync_state["last_sync_at"] if sync_state else None,
        "cached_commits": len(all_commits),
    }
