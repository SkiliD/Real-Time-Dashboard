"""parse the git log read-only, link commits to tasks

Small on purpose -- it has to stay fast.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

from datetime import datetime, timezone

from . import config, events, projects
from .models import GitCommit, day_start_utc, now_iso

MAX_COMMITS_PER_SYNC = 1000


def _to_utc(iso_with_offset: str) -> str:
    """Normalize an ISO-8601 timestamp (e.g. git %cI with +02:00) to UTC +00:00,
    so lexical string comparison/sorting in SQL is chronologically correct."""
    try:
        return datetime.fromisoformat(iso_with_offset).astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return iso_with_offset


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


# ---------------------------------------------------------------------------
# .gitignore management: DevDash must never pollute the USER's project repo.
# The block is marker-delimited so re-running replaces it instead of duplicating.
# ---------------------------------------------------------------------------

IGNORE_BEGIN = "# >>> devdash >>>"
IGNORE_END = "# <<< devdash <<<"

#: lines always ignored (DevDash data/artifacts, wherever they land in the repo)
IGNORE_LINES = [
    ".devdash/",
    "devdash.db",
    "devdash.db-shm",
    "devdash.db-wal",
    ".devdash-snapshot-*.db",
    "devdash-backup-*.zip",
    "devdash-export-*.json",
]


def gitignore_block(home: Path | None = None, repo: Path | None = None) -> list[str]:
    """The marker-delimited .gitignore block. If DEVDASH_HOME lives inside the
    repo, its config.json (API keys!) is ignored by its repo-relative path."""
    lines = list(IGNORE_LINES)
    if home is not None and repo is not None:
        try:
            rel = (home.resolve() / "config.json").relative_to(repo.resolve())
            lines.append(str(rel).replace("\\", "/"))
        except (ValueError, OSError):
            pass  # home outside the repo: nothing extra to ignore
    return [IGNORE_BEGIN, "# DevDash local data — never commit these"] + lines + [IGNORE_END]


def ensure_gitignore(repo_path: str | Path, home: Path | None = None) -> dict:
    """Idempotently add/refresh the DevDash block in <repo>/.gitignore.

    This is the ONLY file DevDash ever writes inside a user's repository, and
    only on explicit request (`devdash git ignore` / GUI button) — it touches
    no git history (read-only rule preserved). Returns what happened."""
    repo = Path(repo_path).expanduser().resolve()
    if not is_repo(repo):
        raise GitError(f"not a git repository: {repo}")
    gi = repo / ".gitignore"
    block = gitignore_block(home=home, repo=repo)
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    # drop any previous marked block (regex with DOTALL between the markers)
    cleaned = re.sub(
        re.escape(IGNORE_BEGIN) + r".*?" + re.escape(IGNORE_END) + r"\n?",
        "", text, flags=re.DOTALL,
    ).rstrip("\n")
    new_text = (cleaned + "\n\n" if cleaned else "") + "\n".join(block) + "\n"
    if gi.exists() and text == new_text:
        return {"updated": False, "path": str(gi), "lines": len(block),
                "already_present": True}
    gi.write_text(new_text, encoding="utf-8")
    return {"updated": True, "path": str(gi), "lines": len(block)}


# --- links (project <-> local repo) ----------------------------------------

def link(conn: sqlite3.Connection, project_id: int, repo_path: str) -> str:
    if projects.get(conn, project_id) is None:
        raise KeyError(f"no project with id {project_id}")
    p = Path(repo_path).expanduser().resolve()
    if not is_repo(p):
        raise GitError(f"not a git repository: {p}")
    with conn:
        conn.execute(
            "INSERT INTO git_links(project_id, repo_path, added_at) VALUES (?, ?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET repo_path = excluded.repo_path",
            (project_id, str(p), now_iso()),
        )
        events.append(conn, "git", project_id, "linked", {"repo_path": str(p)}, commit=False)
    return str(p)


def unlink(conn: sqlite3.Connection, project_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM git_links WHERE project_id = ?", (project_id,))
        events.append(conn, "git", project_id, "unlinked", {}, commit=False)


def get_link(conn: sqlite3.Connection, project_id: int) -> str | None:
    row = conn.execute(
        "SELECT repo_path FROM git_links WHERE project_id = ?", (project_id,)
    ).fetchone()
    return row["repo_path"] if row else None


def linked_repos(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    rows = conn.execute("SELECT project_id, repo_path FROM git_links").fetchall()
    return [(r["project_id"], r["repo_path"]) for r in rows]


# --- commit parsing ----------------------------------------------------------

# Commit subjects are matched against USER-CONFIGURED regexes (config.json):
# cap the matched text so a pathological pattern cannot cause ReDoS on a huge
# commit message (a subject is one line; 500 chars is already generous).
MAX_PARSE_INPUT_CHARS = 500


def parse_message(message: str, patterns: list[str]) -> tuple[int | None, str | None]:
    """Return (task_id, feature) parsed from a commit message, or (None, None).

    First matching pattern wins. A numeric capture group -> task id; otherwise
    the capture is treated as a feature name.
    """
    from .config import _has_nested_quantifier  # local import: avoid a cycle at module load
    haystack = message[:MAX_PARSE_INPUT_CHARS]
    for pattern in patterns:
        if _has_nested_quantifier(pattern):
            # hand-edited config.json bypassed validation: refuse rather than hang
            raise GitError(f"commit pattern {pattern!r} rejected: nested quantifiers (ReDoS risk)")
        try:
            m = re.search(pattern, haystack, re.IGNORECASE)
        except re.error as e:
            raise GitError(f"invalid commit pattern {pattern!r}: {e}") from e
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
    # one subprocess answers everything: repo validity, HEAD hash and branch
    # (in %D, "HEAD -> main" means we are on main; plain "HEAD" = detached)
    out = _git(p, "show", "-s", "--format=%H%x1f%D", "HEAD").strip()
    head, _, refs = out.partition("\x1f")
    head = head.strip()
    branch = "HEAD"
    if "HEAD -> " in refs:
        branch = refs.split("HEAD -> ", 1)[1].split(",", 1)[0].strip() or "HEAD"

    # cheap check: if HEAD did not move AND the branch is unchanged since the
    # last sync, skip the full `git log` (a branch switch with the same HEAD
    # still triggers a sync so the recorded branch stays correct)
    state = conn.execute(
        "SELECT last_seen_hash, branch FROM git_sync_state WHERE repo_path = ?", (str(p),)
    ).fetchone()
    if (state and head and state["last_seen_hash"] == head
            and (state["branch"] or branch) == branch):
        cached = conn.execute(
            "SELECT COUNT(*) AS c FROM git_commits WHERE repo_path = ?", (str(p),)
        ).fetchone()["c"]
        return {"repo_path": str(p), "branch": state_branch(conn, str(p)) or branch,
                "new": 0, "total": cached, "skipped": True}

    raw = _git(
        p, "log", "--format=%H%x1f%h%x1f%an%x1f%s%x1f%cI%x1e", "-n", str(MAX_COMMITS_PER_SYNC)
    )
    new = 0
    total = 0
    pending_events = []
    for record in raw.split("\x1e"):
        record = record.strip("\r\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) != 5:
            continue
        commit_hash, short, author, subject, committed_at = parts
        committed_at = _to_utc(committed_at)  # normalize offset -> UTC for correct sorting
        total += 1
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
            pending_events.append({
                "hash": commit_hash, "short": short, "message": subject,
                "author": author, "branch": branch, "committed_at": committed_at,
                "task_id": task_id, "feature": feature, "repo_path": str(p),
            })
    conn.execute(
        "INSERT INTO git_sync_state(repo_path, last_sync_at, last_seen_hash, branch) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(repo_path) DO UPDATE SET "
        "last_sync_at = excluded.last_sync_at, last_seen_hash = excluded.last_seen_hash, "
        "branch = excluded.branch",
        # store the FULL HEAD hash (not the short one) so the next sync's
        # "HEAD unchanged" cheap-skip actually triggers
        (str(p), now_iso(), head, branch),
    )
    # one transaction for all inserts + all events (instead of 2 fsyncs per commit)
    for ev in pending_events:
        events.append(conn, "git", ev["hash"], "commit", ev, commit=False)
    conn.commit()
    return {"repo_path": str(p), "branch": branch, "new": new, "total": total}


def state_branch(conn: sqlite3.Connection, repo_path: str) -> str | None:
    """Branch recorded at the last sync, if any."""
    row = conn.execute(
        "SELECT branch FROM git_sync_state WHERE repo_path = ?", (repo_path,)
    ).fetchone()
    return row["branch"] if row else None


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
        f"SELECT * FROM git_commits {where} "
        "ORDER BY datetime(committed_at) DESC, hash DESC LIMIT ?",
        (*values, limit),
    ).fetchall()
    return [GitCommit.from_row(r) for r in rows]


def commits_for_task(conn: sqlite3.Connection, task_id: int) -> list[GitCommit]:
    return commits(conn, task_id=task_id, limit=500)


def unparsed_commits(conn: sqlite3.Connection, repo_path: str | None = None) -> list[GitCommit]:
    return commits(conn, repo_path=repo_path, unparsed=True, limit=500)


def status(conn: sqlite3.Connection, project_id: int) -> dict:
    """Git snapshot for a project: link, branch, last commit, today's count.

    Pure DB reads (no git subprocess except one rev-parse if never synced):
    the watcher thread already keeps git_commits / git_sync_state up to date.
    """
    repo = get_link(conn, project_id)
    if repo is None:
        return {"project_id": project_id, "linked": False}

    state = conn.execute(
        "SELECT last_sync_at, last_seen_hash, branch FROM git_sync_state WHERE repo_path = ?",
        (repo,),
    ).fetchone()
    branch = state["branch"] if state and state["branch"] else None
    if branch is None:
        try:  # never synced yet: one cheap subprocess to learn the branch
            branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() or "HEAD"
        except GitError as e:
            return {"project_id": project_id, "linked": True, "repo_path": repo, "error": str(e)}

    # last commit + today's count straight from the cache (no full table scan)
    last = conn.execute(
        "SELECT hash, short, author, message, branch, committed_at, parsed_task_id, "
        "parsed_feature FROM git_commits WHERE repo_path = ? "
        "ORDER BY datetime(committed_at) DESC LIMIT 1",
        (repo,),
    ).fetchone()
    today_count = conn.execute(
        "SELECT COUNT(*) AS c FROM git_commits WHERE repo_path = ? "
        "AND datetime(committed_at) >= datetime(?)",
        (repo, day_start_utc(0)),
    ).fetchone()["c"]
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM git_commits WHERE repo_path = ?", (repo,)
    ).fetchone()["c"]

    return {
        "project_id": project_id, "linked": True, "repo_path": repo,
        "branch": branch,
        "last_commit": dict(last) if last else None,
        "commits_today": today_count,
        "last_sync_at": state["last_sync_at"] if state else None,
        "cached_commits": total,
    }
