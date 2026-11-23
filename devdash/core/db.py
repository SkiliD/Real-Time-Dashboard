"""sqlite connection, WAL, schema bootstrap + migrations

If it breaks, the tests will say so.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 5
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (and create if needed) the database at db_path."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # busy_timeout: with the threaded GUI server + git watcher + CLI running
    # concurrently, a second writer must WAIT instead of failing immediately
    # with "database is locked" (default timeout is 0).
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        # WAL: readers (GUI polling) never block writers (CLI actions).
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass  # e.g. on filesystems without WAL support; plain journal is fine
    try:
        # the DB holds the whole dev history: restrict to the current user
        # (best effort — chmod is a no-op-ish on Windows, ACLs would be stricter)
        Path(db_path).chmod(0o600)
    except OSError:
        pass
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply schema.sql (idempotent), run lightweight migrations, stamp version.

    Note: executescript() commits implicitly, but schema.sql is fully
    idempotent (IF NOT EXISTS), so re-running after a crash is safe. The
    destructive part (migrations: DROP/ALTER) + the version stamp run in ONE
    transaction so a crash mid-migration cannot leave a half-migrated schema.
    """
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    with conn:  # one transaction: migrations + version stamp
        _migrate(conn)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )


# sqlite has no "ADD COLUMN IF NOT EXISTS" on older versions, hence this dance
def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created by older versions.

    Version 0 = fresh database (schema.sql already applied by init_db), so
    migrations only run when 0 < version < SCHEMA_VERSION.
    """
    version = schema_version(conn)
    if version == 0:
        return  # fresh database: schema.sql already applied; stamp done by caller
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "planned_min" not in cols:  # v2
        conn.execute("ALTER TABLE sessions ADD COLUMN planned_min INTEGER")
    if version < 3:
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
    if version < 4:
        # v4: extra lookup indexes + cached git branch in git_sync_state
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_tasks_feature ON tasks(feature_id)",
            "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_task ON sessions(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_log_task ON log_entries(task_id)",
        ):
            conn.execute(stmt)
        sync_cols = {r["name"] for r in conn.execute("PRAGMA table_info(git_sync_state)").fetchall()}
        if "branch" not in sync_cols:
            conn.execute("ALTER TABLE git_sync_state ADD COLUMN branch TEXT")
    if version < 5:
        # v5: per-project programming languages (JSON array)
        proj_cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "languages" not in proj_cols:
            conn.execute("ALTER TABLE projects ADD COLUMN languages TEXT NOT NULL DEFAULT '[]'")
    # no commit here: init_db wraps _migrate in a single transaction


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0
