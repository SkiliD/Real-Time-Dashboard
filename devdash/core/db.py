"""SQLite connection + schema bootstrap for the DevDash core.

Stdlib only. One database file (devdash.db) inside DEVDASH_HOME, so the whole
app state is a single portable file. WAL mode allows the CLI and the GUI to
read/write concurrently without locking each other out.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (and create if needed) the database at db_path."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # WAL: readers (GUI polling) never block writers (CLI actions).
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass  # e.g. on filesystems without WAL support; plain journal is fine
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply schema.sql (idempotent), run lightweight migrations, stamp version."""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    _migrate(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created by older versions."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "planned_min" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN planned_min INTEGER")
    if schema_version(conn) < 3:
        # v3: git_commits.parsed_task_id no longer has a FK (a commit may
        # reference a task id that does not exist yet). Recreate the cache
        # table; it is rebuilt by the next `git sync`.
        conn.execute("DROP TABLE IF EXISTS git_commits")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS git_commits ("
            "hash TEXT PRIMARY KEY, repo_path TEXT NOT NULL, short TEXT NOT NULL, "
            "author TEXT NOT NULL, message TEXT NOT NULL, branch TEXT, "
            "committed_at TEXT NOT NULL, parsed_task_id INTEGER, parsed_feature TEXT)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_commits_repo ON git_commits(repo_path)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_commits_task ON git_commits(parsed_task_id)"
        )
    conn.commit()


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0
