"""devlog entries"""

from __future__ import annotations

import json
import sqlite3

from . import events, i18n, projects
from .models import LogEntry, check_text, escape_like, now_iso

MAX_LIST_ROWS = 100_000  # hard cap for list-style queries (CLI --limit included)


def add(
    conn: sqlite3.Connection,
    content: str,
    project_id: int | None = None,
    task_id: int | None = None,
    tags: list[str] | tuple[str, ...] = (),
) -> LogEntry:
    content = check_text(content.strip(), "content")
    if not content:
        raise ValueError(i18n.tr("err.log_empty"))
    if project_id is None:
        active = projects.get_active(conn)
        if active is None:
            raise KeyError(i18n.tr("err.no_active_project"))
        project_id = active.id
    elif projects.get(conn, project_id) is None:
        raise KeyError(i18n.tr("err.no_project_id", id=project_id))
    if task_id is not None:
        from . import tasks as _tasks
        if _tasks.get(conn, task_id) is None:
            raise KeyError(i18n.tr("err.no_task", id=task_id))
    now = now_iso()
    with conn:  # row + event in one transaction
        cur = conn.execute(
            "INSERT INTO log_entries(project_id, task_id, content, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, task_id, content, json.dumps([str(t) for t in tags]), now),
        )
        entry = get(conn, cur.lastrowid)
        assert entry is not None
        events.append(conn, "log", entry.id, "created", entry.to_dict(), commit=False)
    return entry


def get(conn: sqlite3.Connection, entry_id: int) -> LogEntry | None:
    row = conn.execute("SELECT * FROM log_entries WHERE id = ?", (entry_id,)).fetchone()
    return LogEntry.from_row(row) if row else None


def list(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    task_id: int | None = None,
    query: str | None = None,
    limit: int = 200,
    trash: bool = False,            # True = only soft-deleted (corbeille)
) -> list[LogEntry]:
    """Entries, newest first. `query` = case-insensitive substring on content.
    Soft-deleted entries are excluded unless trash=True."""
    clauses, values = [], []
    clauses.append("deleted_at IS NOT NULL" if trash else "deleted_at IS NULL")
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    if task_id is not None:
        clauses.append("task_id = ?")
        values.append(task_id)
    if query:
        # escape LIKE wildcards in user input, keep case-insensitive substring match
        clauses.append("content LIKE ? ESCAPE '\\'")
        values.append(f"%{escape_like(query)}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = min(limit, MAX_LIST_ROWS)  # CLI --limit must not materialize everything
    rows = conn.execute(
        f"SELECT * FROM log_entries {where} ORDER BY created_at DESC, id DESC LIMIT ?",
        (*values, limit),
    ).fetchall()
    return [LogEntry.from_row(r) for r in rows]


def search(conn: sqlite3.Connection, text: str, project_id: int | None = None, limit: int = 200) -> list[LogEntry]:
    return list(conn, project_id=project_id, query=text, limit=limit)


def update(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    content: str | None = None,
    tags: list[str] | None = None,
) -> LogEntry:
    entry = get(conn, entry_id)
    if entry is None:
        raise KeyError(i18n.tr("err.no_log_entry", id=entry_id))
    fields, values = [], []
    if content is not None:
        content = check_text(content, "content")
        if not content.strip():
            raise ValueError(i18n.tr("err.log_empty"))
        fields.append("content = ?")
        values.append(content.strip())
    if tags is not None:
        fields.append("tags = ?")
        values.append(json.dumps([str(t) for t in tags]))
    if fields:
        values.append(entry_id)
        with conn:  # row + event in one transaction
            conn.execute(f"UPDATE log_entries SET {', '.join(fields)} WHERE id = ?", values)
            entry = get(conn, entry_id)
            assert entry is not None
            events.append(conn, "log", entry.id, "updated", entry.to_dict(), commit=False)
        return entry
    entry = get(conn, entry_id)
    assert entry is not None
    return entry


def delete(conn: sqlite3.Connection, entry_id: int, permanent: bool = False) -> LogEntry:
    """Delete a devlog entry. Default = SOFT delete (corbeille, restorable via
    restore()); permanent=True = hard delete (events cleaned up too)."""
    entry = get(conn, entry_id)
    if entry is None:
        raise KeyError(i18n.tr("err.no_log_entry", id=entry_id))
    if permanent:
        with conn:  # one transaction: bus cleanup + row delete
            conn.execute("DELETE FROM events WHERE entity = 'log' AND entity_id = ?", (entry.id,))
            conn.execute("DELETE FROM log_entries WHERE id = ?", (entry.id,))
        events.append(conn, "log", entry.id, "deleted", {"id": entry.id, "permanent": True})
        return entry
    with conn:
        conn.execute("UPDATE log_entries SET deleted_at = ? WHERE id = ?", (now_iso(), entry.id))
    events.append(conn, "log", entry.id, "deleted", {"id": entry.id, "soft": True})
    return entry


def restore(conn: sqlite3.Connection, entry_id: int) -> LogEntry:
    """Restore a soft-deleted devlog entry."""
    entry = get(conn, entry_id)
    if entry is None:
        raise KeyError(i18n.tr("err.no_log_entry", id=entry_id))
    with conn:
        conn.execute("UPDATE log_entries SET deleted_at = NULL WHERE id = ?", (entry.id,))
    events.append(conn, "log", entry.id, "restored", {"id": entry.id})
    return get(conn, entry_id)
