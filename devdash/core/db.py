"""SQLite connection helper: WAL mode, busy timeout, schema bootstrap + migrations.

"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 11
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


SQLITE_TIMEOUT_SEC = 10.0  # same value for connect timeout and busy_timeout


def connect(db_path: str | Path, *, setup: bool = True) -> sqlite3.Connection:
    """Open (and create if needed) the database at db_path.

    setup=True (default): one-time-per-file work — WAL journal mode (persistent
    in the DB header, so pooled connections can skip it) + chmod 600.
    setup=False: light hot path for pooled per-thread connections — skips the
    chmod syscall and the WAL pragma (it only takes a write lock briefly)."""
    conn = sqlite3.connect(str(db_path), timeout=SQLITE_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # busy_timeout: with the threaded GUI server + git watcher + CLI running
    # concurrently, a second writer must WAIT instead of failing immediately
    # with "database is locked" (default timeout is 0).
    conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_TIMEOUT_SEC * 1000)}")
    if setup:
        try:
            # WAL: readers (GUI polling) never block writers (CLI actions).
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass  # e.g. on filesystems without WAL support; plain journal is fine
        restrict_permissions(Path(db_path))
    return conn


def restrict_permissions(path: Path) -> None:
    """Best-effort user-only permissions for sensitive files (db, config.json,
    backups): chmod 0600 on POSIX, plus an icacls ACL reset on Windows where
    chmod alone does not remove inherited ACLs. Never raises."""
    import subprocess

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if os.name == "nt":
        user = os.environ.get("USERNAME") or ""
        if user:
            try:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    capture_output=True, timeout=10, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass


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
    if version < 6:
        # v6: connected project folder (empty = none)
        proj_cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "path" not in proj_cols:
            conn.execute("ALTER TABLE projects ADD COLUMN path TEXT NOT NULL DEFAULT ''")
    if version < 7:
        # v7: hot-path indexes — running session lookup (partial) + commit
        # ordering/counting by date (committed_at is normalized UTC ISO, so a
        # plain lexicographic index is chronologically correct)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_running ON sessions(id) WHERE ended_at IS NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_commits_repo_date ON git_commits(repo_path, committed_at)"
        )
    if version < 8:
        # v8: persisted AI chat history (per project)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ai_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, "
            "role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_messages_project ON ai_messages(project_id, id)"
        )
    if version < 9:
        # v9: soft delete (corbeille) for tasks / log_entries / features
        for table in ("tasks", "log_entries", "features"):
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "deleted_at" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TEXT")
    if version < 10:
        # v10: poll-path indexes. The GUI polls /api/logs and /api/sessions every
        # 1.5 s with ORDER BY created_at/started_at DESC LIMIT n per project —
        # without these the sort needs a full table scan + temp b-tree each poll.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_log_project_created "
            "ON log_entries(project_id, created_at DESC, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_project_started "
            "ON sessions(project_id, started_at DESC, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_project_task "
            "ON sessions(project_id, task_id)"
        )
        # global commit ordering (project=all view) and done-task counting
        conn.execute("CREATE INDEX IF NOT EXISTS idx_commits_date ON git_commits(committed_at DESC)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_completed "
            "ON tasks(completed_at) WHERE status = 'done' AND completed_at IS NOT NULL"
        )
    if version < 11:
        # v11: poll-path sort indexes — tasks ORDER BY CASE status/priority (expression
        # index must match the ORDER BY exactly), commits with the hash tiebreak
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_sort ON tasks("
            "project_id, "
            "CASE status WHEN 'in_progress' THEN 0 WHEN 'todo' THEN 1 WHEN 'done' THEN 2 ELSE 3 END, "
            "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
            "order_index, id) WHERE deleted_at IS NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_commits_repo_date_hash "
            "ON git_commits(repo_path, committed_at DESC, hash DESC)"
        )
    # no commit here: init_db wraps _migrate in a single transaction


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0
