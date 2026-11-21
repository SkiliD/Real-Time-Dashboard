"""Devlog entries (core): dated, searchable long-term memory.

Entries attach to the active project by default, optionally to a task.
They are never hard-deleted (append-only journal); edits go through update().
"""

from __future__ import annotations

import json
import sqlite3

from . import events, projects
from .models import LogEntry, now_iso


def add(
    conn: sqlite3.Connection,
    content: str,
    project_id: int | None = None,
    task_id: int | None = None,
    tags: list[str] | tuple[str, ...] = (),
) -> LogEntry:
    content = content.strip()
    if not content:
        raise ValueError("log entry content is empty")
    if project_id is None:
        active = projects.get_active(conn)
        if active is None:
            raise KeyError("no active project (set one with 'devdash projects set-active')")
        project_id = active.id
    elif projects.get(conn, project_id) is None:
        raise KeyError(f"no project with id {project_id}")
    if task_id is not None:
        from . import tasks as _tasks
        if _tasks.get(conn, task_id) is None:
            raise KeyError(f"no task #{task_id}")
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO log_entries(project_id, task_id, content, tags, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, task_id, content, json.dumps([str(t) for t in tags]), now),
    )
    conn.commit()
    entry = get(conn, cur.lastrowid)
    assert entry is not None
    events.append(conn, "log", entry.id, "created", entry.to_dict())
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
) -> list[LogEntry]:
    """Entries, newest first. `query` = case-insensitive substring on content."""
    clauses, values = [], []
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    if task_id is not None:
        clauses.append("task_id = ?")
        values.append(task_id)
    if query:
        # escape LIKE wildcards in user input, keep case-insensitive substring match
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("content LIKE ? ESCAPE '\\'")
        values.append(f"%{escaped}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
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
        raise KeyError(f"no log entry #{entry_id}")
    fields, values = [], []
    if content is not None:
        if not content.strip():
            raise ValueError("log entry content is empty")
        fields.append("content = ?")
        values.append(content.strip())
    if tags is not None:
        fields.append("tags = ?")
        values.append(json.dumps([str(t) for t in tags]))
    if fields:
        values.append(entry_id)
        conn.execute(f"UPDATE log_entries SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    entry = get(conn, entry_id)
    assert entry is not None
    events.append(conn, "log", entry.id, "updated", entry.to_dict())
    return entry
