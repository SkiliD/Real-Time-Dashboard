from __future__ import annotations

import json
import sqlite3

from . import events, projects
from .models import MAX_TITLE_LEN, Task, check_text, escape_like, now_iso

VALID_PRIORITIES = ("critical", "high", "medium", "low")
VALID_STATUSES = ("todo", "in_progress", "done", "cancelled")


class _Unset:
    """Sentinel: distinguishes "argument not provided" from None (= set to NULL)."""


_UNSET = _Unset()


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
    if not isinstance(title, str) or not title.strip():
        raise ValueError("task title is required (non-empty)")
    title = check_text(title.strip(), "title", MAX_TITLE_LEN)
    description = check_text(description or "", "description")
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority {priority!r}, expected one of {VALID_PRIORITIES}")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}, expected one of {VALID_STATUSES}")
    if estimate_min is not None and (isinstance(estimate_min, bool) or not isinstance(estimate_min, int) or estimate_min < 0):
        raise ValueError("estimate_min must be a positive integer or None")
    if projects.get(conn, project_id) is None:
        raise KeyError(f"no project with id {project_id}")
    # a provisional task for _check_parent (only id/project_id matter there)
    if parent_id is not None or feature_id is not None:
        probe = Task(id=0, project_id=project_id, parent_id=None, title=title,
                     description="", priority=priority, estimate_min=None,
                     status=status, tags=[], feature_id=None)
        if parent_id is not None:
            _check_parent(conn, probe, parent_id)
        if feature_id is not None:
            from . import features as _feats
            f = _feats.get(conn, feature_id)
            if f is None:
                raise KeyError(f"no feature #{feature_id}")
            if f.project_id != project_id:
                raise ValueError(f"feature #{feature_id} belongs to another project")
    now = now_iso()
    with conn:  # one transaction: row + event (a crash can't leave one without the other)
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
                json.dumps([str(t) for t in tags]),
                feature_id,
                now,
                now,
                now if status == "done" else None,
            ),
        )
        task = get(conn, cur.lastrowid)
        assert task is not None
        events.append(conn, "task", task.id, "created", task.to_dict(), commit=False)
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
    q: str | None = None,           # case-insensitive substring on title/description
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
        # tags are stored as a JSON array; exact match via json_each (no LIKE
        # wildcards, safe for tags containing % _ or quotes)
        clauses.append("EXISTS (SELECT 1 FROM json_each(tasks.tags) WHERE json_each.value = ?)")
        values.append(tag)
    if parent_id == "root":
        clauses.append("parent_id IS NULL")
    elif parent_id is not None:
        clauses.append("parent_id = ?")
        values.append(parent_id)
    if feature_id is not None:
        clauses.append("feature_id = ?")
        values.append(feature_id)
    if q:
        escaped = escape_like(q)
        clauses.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
        values.extend([f"%{escaped}%", f"%{escaped}%"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    priority_order = "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"
    status_order = "CASE status WHEN 'in_progress' THEN 0 WHEN 'todo' THEN 1 WHEN 'done' THEN 2 ELSE 3 END"
    rows = conn.execute(
        f"SELECT * FROM tasks {where} ORDER BY {status_order}, {priority_order}, order_index, id",
        values,
    ).fetchall()
    return [Task.from_row(r) for r in rows]


def delete(conn: sqlite3.Connection, task_id: int) -> Task:
    """Hard-delete a task (explicit human action; the GUI/CLI confirm first).

    Subtasks cascade (parent_id ON DELETE CASCADE), feature_id is set to NULL,
    and git commit references stay (no FK by design). The task's events are
    cleaned up in the same transaction.
    """
    task = get(conn, task_id)
    if task is None:
        raise KeyError(f"no task with id {task_id}")
    with conn:  # one transaction: bus cleanup + row delete (subtasks cascade)
        conn.execute("DELETE FROM events WHERE entity = 'task' AND entity_id = ?", (task.id,))
        conn.execute("DELETE FROM tasks WHERE id = ?", (task.id,))
    events.append(conn, "task", task.id, "deleted", {"id": task.id})
    return task


def set_status(conn: sqlite3.Connection, task_id: int, status: str) -> Task:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}, expected one of {VALID_STATUSES}")
    task = get(conn, task_id)
    if task is None:
        raise KeyError(f"no task with id {task_id}")
    now = now_iso()
    with conn:  # row + event in one transaction
        conn.execute(
            "UPDATE tasks SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?",
            (status, now if status == "done" else None, now, task_id),
        )
        task = get(conn, task_id)
        assert task is not None
        events.append(conn, "task", task.id, f"status:{status}", task.to_dict(), commit=False)
    return task


def start(conn: sqlite3.Connection, task_id: int) -> Task:
    """Mark a task in progress. Solo focus: the project's other in_progress
    tasks go back to todo (one active task per project)."""
    task = get(conn, task_id)
    if task is None:
        raise KeyError(f"no task with id {task_id}")
    now = now_iso()
    with conn:  # demote others + promote this one in ONE transaction (solo-focus rule)
        conn.execute(
            "UPDATE tasks SET status = 'todo', updated_at = ? "
            "WHERE project_id = ? AND status = 'in_progress' AND id != ?",
            (now, task.project_id, task_id),
        )
        conn.execute(
            "UPDATE tasks SET status = 'in_progress', completed_at = NULL, updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        task = get(conn, task_id)
        assert task is not None
        events.append(conn, "task", task.id, "status:in_progress", task.to_dict(), commit=False)
    return task


def done(conn: sqlite3.Connection, task_id: int) -> Task:
    return set_status(conn, task_id, "done")


def cancel(conn: sqlite3.Connection, task_id: int) -> Task:
    return set_status(conn, task_id, "cancelled")


def _check_parent(conn: sqlite3.Connection, task: Task, parent_id: int | None) -> None:
    """Validate a new parent: exists, same project, and no cycle (incl. self)."""
    if parent_id is None:
        return
    if parent_id == task.id:
        raise ValueError(f"task #{task.id} cannot be its own parent")
    parent = get(conn, parent_id)
    if parent is None:
        raise KeyError(f"no parent task #{parent_id}")
    if parent.project_id != task.project_id:
        raise ValueError(f"parent task #{parent_id} belongs to another project")
    # walk ancestors of the proposed parent; if we reach this task, it's a cycle
    seen = {task.id}
    cur = parent
    while cur is not None and cur.parent_id is not None:
        if cur.parent_id in seen:
            raise ValueError(f"parenting task #{task.id} under #{parent_id} would create a cycle")
        seen.add(cur.parent_id)
        cur = get(conn, cur.parent_id)


def update(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    title: str | None = _UNSET,
    description: str | None = _UNSET,
    priority: str | None = _UNSET,
    estimate_min: int | None = _UNSET,
    tags: list[str] | None = _UNSET,
    parent_id: int | None = _UNSET,
    feature_id: int | None = _UNSET,
) -> Task:
    """Update fields. _UNSET (default) = leave unchanged; None = set to NULL."""
    task = get(conn, task_id)
    if task is None:
        raise KeyError(f"no task with id {task_id}")
    if priority is not _UNSET and priority is not None and priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority {priority!r}")
    # NOT NULL columns: None is only valid for nullable ones (description,
    # estimate_min, parent_id, feature_id) — an explicit None on title/priority
    # is a caller bug, fail loudly instead of a raw IntegrityError.
    if title is not _UNSET and title is None:
        raise ValueError("title cannot be null")
    if priority is not _UNSET and priority is None:
        raise ValueError("priority cannot be null")
    if title is not _UNSET and title is not None:
        title = check_text(title.strip(), "title", MAX_TITLE_LEN)
    if description is not _UNSET and description is not None:
        description = check_text(description, "description")
    if parent_id is not _UNSET:
        _check_parent(conn, task, parent_id)
    fields, values = [], []
    for column, value in (
        ("title", title),
        ("description", description),
        ("priority", priority),
        ("estimate_min", estimate_min),
        ("tags", json.dumps([str(t) for t in tags]) if tags is not _UNSET else _UNSET),
        ("parent_id", parent_id),
        ("feature_id", feature_id),
    ):
        if value is not _UNSET:
            fields.append(f"{column} = ?")
            values.append(value)  # None is stored as NULL here
    if fields:
        fields.append("updated_at = ?")
        values.append(now_iso())
        values.append(task_id)
        with conn:  # row + event in one transaction
            conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", values)
            task = get(conn, task_id)
            assert task is not None
            events.append(conn, "task", task.id, "updated", task.to_dict(), commit=False)
    return task


def list_tags(conn: sqlite3.Connection, project_id: int | None = None) -> list[str]:
    """All distinct tags, computed in SQL (json_each) instead of loading every task."""
    if project_id is None:
        rows = conn.execute(
            "SELECT DISTINCT json_each.value AS v FROM tasks, json_each(tasks.tags) "
            "ORDER BY v"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT json_each.value AS v FROM tasks, json_each(tasks.tags) "
            "WHERE project_id = ? ORDER BY v",
            (project_id,),
        ).fetchall()
    return [r["v"] for r in rows]
