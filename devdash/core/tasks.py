"""Tasks CRUD, filters, subtasks and status transitions.

Solo-focus rule: starting a task moves the project's other in_progress tasks
back to todo, so there is at most one "in progress" task per project.
"""

from __future__ import annotations

import json
import sqlite3

from . import events, projects
from .models import Task, now_iso

VALID_PRIORITIES = ("critical", "high", "medium", "low")
VALID_STATUSES = ("todo", "in_progress", "done", "cancelled")


def add(
    conn: sqlite3.Connection,
    project_id: int,
    title: str,
    description: str = "",
    priority: str = "medium",
    estimate_min: int | None = None,
    tags: list[str] | tuple[str, ...] = (),
    parent_id: int | None = None,
    feature_id: int | None = None,
    status: str = "todo",
) -> Task:
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority {priority!r}, expected one of {VALID_PRIORITIES}")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}, expected one of {VALID_STATUSES}")
    if projects.get(conn, project_id) is None:
        raise KeyError(f"no project with id {project_id}")
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO tasks(project_id, parent_id, title, description, priority, "
        "estimate_min, status, tags, feature_id, order_index, created_at, updated_at, "
        "completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (
            project_id,
            parent_id,
            title,
            description,
            priority,
            estimate_min,
            status,
            json.dumps([str(t) for t in tags]),  # NB: module-level list() shadows builtin
            feature_id,
            now,
            now,
            now if status == "done" else None,
        ),
    )
    conn.commit()
    task = get(conn, cur.lastrowid)
    assert task is not None
    events.append(conn, "task", task.id, "created", task.to_dict())
    return task


def get(conn: sqlite3.Connection, task_id: int) -> Task | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return Task.from_row(row) if row else None


def list(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    status: str | list[str] | None = None,
    priority: str | None = None,
    tag: str | None = None,
    parent_id: int | None = None,   # None = all; use "root" for top-level only
    feature_id: int | None = None,
    include_cancelled: bool = True,
) -> list[Task]:
    """List tasks with combinable filters, ordered by status then priority."""
    clauses, values = [], []
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    if status is not None:
        statuses = [status] if isinstance(status, str) else [str(s) for s in status]
        clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
        values.extend(statuses)
    if not include_cancelled:
        clauses.append("status != 'cancelled'")
    if priority is not None:
        clauses.append("priority = ?")
        values.append(priority)
    if tag is not None:
        # tags stored as JSON array; match with LIKE on the serialized form
        clauses.append("tags LIKE ?")
        values.append(f'%"{tag}"%')
    if parent_id == "root":
        clauses.append("parent_id IS NULL")
    elif parent_id is not None:
        clauses.append("parent_id = ?")
        values.append(parent_id)
    if feature_id is not None:
        clauses.append("feature_id = ?")
        values.append(feature_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    priority_order = "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"
    status_order = "CASE status WHEN 'in_progress' THEN 0 WHEN 'todo' THEN 1 WHEN 'done' THEN 2 ELSE 3 END"
    rows = conn.execute(
        f"SELECT * FROM tasks {where} ORDER BY {status_order}, {priority_order}, order_index, id",
        values,
    ).fetchall()
    return [Task.from_row(r) for r in rows]


def set_status(conn: sqlite3.Connection, task_id: int, status: str) -> Task:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}, expected one of {VALID_STATUSES}")
    task = get(conn, task_id)
    if task is None:
        raise KeyError(f"no task with id {task_id}")
    now = now_iso()
    conn.execute(
        "UPDATE tasks SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
        (status, now if status == "done" else None, now, task_id),
    )
    conn.commit()
    task = get(conn, task_id)
    assert task is not None
    events.append(conn, "task", task.id, f"status:{status}", task.to_dict())
    return task


def start(conn: sqlite3.Connection, task_id: int) -> Task:
    """Mark a task in progress. Solo focus: the project's other in_progress
    tasks go back to todo (one active task per project)."""
    task = get(conn, task_id)
    if task is None:
        raise KeyError(f"no task with id {task_id}")
    now = now_iso()
    conn.execute(
        "UPDATE tasks SET status = 'todo', updated_at = ? "
        "WHERE project_id = ? AND status = 'in_progress' AND id != ?",
        (now, task.project_id, task_id),
    )
    conn.commit()
    return set_status(conn, task_id, "in_progress")


def done(conn: sqlite3.Connection, task_id: int) -> Task:
    return set_status(conn, task_id, "done")


def cancel(conn: sqlite3.Connection, task_id: int) -> Task:
    return set_status(conn, task_id, "cancelled")


def update(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    estimate_min: int | None = None,
    tags: list[str] | None = None,
    parent_id: int | None = None,
    feature_id: int | None = None,
) -> Task:
    task = get(conn, task_id)
    if task is None:
        raise KeyError(f"no task with id {task_id}")
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority {priority!r}")
    fields, values = [], []
    for column, value in (
        ("title", title),
        ("description", description),
        ("priority", priority),
        ("estimate_min", estimate_min),
        ("tags", json.dumps([str(t) for t in tags]) if tags is not None else None),
        ("parent_id", parent_id),
        ("feature_id", feature_id),
    ):
        if value is not None:
            fields.append(f"{column} = ?")
            values.append(value)
    if fields:
        fields.append("updated_at = ?")
        values.append(now_iso())
        values.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    task = get(conn, task_id)
    assert task is not None
    events.append(conn, "task", task.id, "updated", task.to_dict())
    return task


def list_tags(conn: sqlite3.Connection, project_id: int | None = None) -> list[str]:
    tasks_in_scope = list(conn, project_id=project_id)
    seen: dict[str, None] = {}
    for t in tasks_in_scope:
        for tag in t.tags:
            seen.setdefault(tag, None)
    return sorted(seen)
