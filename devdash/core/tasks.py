from __future__ import annotations

import builtins
import json
import sqlite3

from . import events, i18n, projects
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
        raise ValueError(i18n.tr("err.task_title_required"))
    title = check_text(title.strip(), "title", MAX_TITLE_LEN)
    description = check_text(description or "", "description")
    if priority not in VALID_PRIORITIES:
        raise ValueError(i18n.tr("err.priority_invalid", priority=priority, items=VALID_PRIORITIES))
    if status not in VALID_STATUSES:
        raise ValueError(i18n.tr("err.status_invalid", status=status, items=VALID_STATUSES))
    if estimate_min is not None and (isinstance(estimate_min, bool) or not isinstance(estimate_min, int) or estimate_min < 0):
        raise ValueError(i18n.tr("err.estimate_invalid"))
    if projects.get(conn, project_id) is None:
        raise KeyError(i18n.tr("err.no_project_id", id=project_id))
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
                raise KeyError(i18n.tr("err.no_feature_id", id=feature_id))
            if f.project_id != project_id:
                raise ValueError(i18n.tr("err.feature_other_project", id=feature_id))
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


def count_by_status(conn: sqlite3.Connection, project_id: int, statuses: list[str]) -> int:
    """COUNT only (no row loading) — /api/status is polled every 1.5 s."""
    ph = ",".join("?" for _ in statuses)
    return conn.execute(
        f"SELECT COUNT(*) FROM tasks WHERE project_id = ? AND deleted_at IS NULL AND status IN ({ph})",
        [project_id, *statuses],
    ).fetchone()[0]


def list_brief(conn: sqlite3.Connection, project_id: int, status: str) -> list[dict]:
    """id/title only, for header selects — full rows come from list()."""
    rows = conn.execute(
        "SELECT id, title FROM tasks WHERE project_id = ? AND deleted_at IS NULL AND status = ? ORDER BY id",
        (project_id, status),
    ).fetchall()
    return [{"id": r["id"], "title": r["title"]} for r in rows]


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
    limit: int | None = None,       # None = no LIMIT (internal callers); the API always clamps
    trash: bool = False,            # True = only soft-deleted (corbeille); False = only visible
) -> list[Task]:
    """List tasks with combinable filters, ordered by status then priority.
    Soft-deleted tasks (deleted_at set) are excluded unless trash=True."""
    clauses, values = [], []
    clauses.append("deleted_at IS NOT NULL" if trash else "deleted_at IS NULL")
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
    sql = f"SELECT * FROM tasks {where} ORDER BY {status_order}, {priority_order}, order_index, id"
    if limit is not None:
        sql += " LIMIT ?"
        values = [*values, int(limit)]
    rows = conn.execute(sql, values).fetchall()
    return [Task.from_row(r) for r in rows]


def _descendant_ids(conn: sqlite3.Connection, root_id: int) -> list[int]:
    """root_id + all its descendants (recursive), for soft-delete/restore cascades."""
    rows = conn.execute(
        "WITH RECURSIVE d(id) AS (SELECT ? UNION ALL "
        "SELECT t.id FROM tasks t JOIN d ON t.parent_id = d.id) SELECT id FROM d",
        (root_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def delete(conn: sqlite3.Connection, task_id: int, permanent: bool = False) -> Task:
    """Delete a task. Default = SOFT delete (corbeille): the task and its
    subtasks get deleted_at set and can be restored with restore().
    permanent=True = hard delete (explicit; subtasks cascade via FK, feature_id
    is set to NULL, git commit references stay, events cleaned up)."""
    task = get(conn, task_id)
    if task is None:
        raise KeyError(i18n.tr("err.no_task", id=task_id))
    if permanent:
        with conn:  # one transaction: bus cleanup + row delete (subtasks cascade)
            conn.execute("DELETE FROM events WHERE entity = 'task' AND entity_id = ?", (task.id,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (task.id,))
        events.append(conn, "task", task.id, "deleted", {"id": task.id, "permanent": True})
        return task
    ids = _descendant_ids(conn, task_id)
    now = now_iso()
    with conn:
        q = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE tasks SET deleted_at = ?, updated_at = ? WHERE id IN ({q})",
            (now, now, *ids),
        )
    events.append(conn, "task", task.id, "deleted", {"id": task.id, "soft": True, "count": len(ids)})
    return task


def restore(conn: sqlite3.Connection, task_id: int) -> Task:
    """Restore a soft-deleted task (and the subtasks deleted with it)."""
    task = get(conn, task_id)
    if task is None:
        raise KeyError(i18n.tr("err.no_task", id=task_id))
    ids = _descendant_ids(conn, task_id)
    now = now_iso()
    with conn:
        q = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE tasks SET deleted_at = NULL, updated_at = ? WHERE id IN ({q})",
            (now, *ids),
        )
    events.append(conn, "task", task.id, "restored", {"id": task.id, "count": len(ids)})
    return get(conn, task_id)


def set_status(conn: sqlite3.Connection, task_id: int, status: str) -> Task:
    if status not in VALID_STATUSES:
        raise ValueError(i18n.tr("err.status_invalid", status=status, items=VALID_STATUSES))
    task = get(conn, task_id)
    if task is None:
        raise KeyError(i18n.tr("err.no_task", id=task_id))
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
        raise KeyError(i18n.tr("err.no_task", id=task_id))
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
        raise ValueError(i18n.tr("err.self_parent", id=task.id))
    parent = get(conn, parent_id)
    if parent is None:
        raise KeyError(i18n.tr("err.no_parent_task", id=parent_id))
    if parent.project_id != task.project_id:
        raise ValueError(i18n.tr("err.parent_other_project", id=parent_id))
    # walk ancestors of the proposed parent; if we reach this task, it's a cycle
    seen = {task.id}
    cur = parent
    while cur is not None and cur.parent_id is not None:
        if cur.parent_id in seen:
            raise ValueError(i18n.tr("err.parent_cycle", id=task.id, parent=parent_id))
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
        raise KeyError(i18n.tr("err.no_task", id=task_id))
    if priority is not _UNSET and priority is not None and priority not in VALID_PRIORITIES:
        raise ValueError(i18n.tr("err.priority_invalid_short", priority=priority))
    # same estimate_min validation as add(): a negative/non-int estimate would
    # silently corrupt stats.estimates ratios and project_progress weights
    if estimate_min is not _UNSET and estimate_min is not None and (
            isinstance(estimate_min, bool) or not isinstance(estimate_min, int) or estimate_min < 0):
        raise ValueError(i18n.tr("err.estimate_invalid"))
    # NOT NULL columns: None is only valid for nullable ones (description,
    # estimate_min, parent_id, feature_id) — an explicit None on title/priority
    # is a caller bug, fail loudly instead of a raw IntegrityError.
    if title is not _UNSET and title is None:
        raise ValueError(i18n.tr("err.title_null"))
    if priority is not _UNSET and priority is None:
        raise ValueError(i18n.tr("err.priority_null"))
    if title is not _UNSET and title is not None:
        title = check_text(title.strip(), "title", MAX_TITLE_LEN)
    if description is not _UNSET and description is not None:
        description = check_text(description, "description")
    if parent_id is not _UNSET:
        _check_parent(conn, task, parent_id)
    # NB: this module defines its own list() function, hence builtins.list here
    if tags is not _UNSET and tags is not None and not isinstance(tags, (builtins.list, tuple)):
        raise ValueError(i18n.tr("err.tags_type"))
    fields, values = [], []
    for column, value in (
        ("title", title),
        ("description", description),
        ("priority", priority),
        ("estimate_min", estimate_min),
        # None = clear to [] (column is NOT NULL; an explicit null would TypeError)
        ("tags", _UNSET if tags is _UNSET else json.dumps([str(t) for t in (tags or [])])),
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


# ---------------------------------------------------------------------------
# Subtask stacking (Trello/Notion-style): descendants, rollup progress, tree
# ---------------------------------------------------------------------------

def descendants(conn: sqlite3.Connection, task_id: int) -> list[int]:
    """All descendant ids of a task (BFS, cycle-safe via a seen set).
    Empty list for a leaf or an unknown id."""
    out, queue, seen = [], [task_id], {task_id}
    while queue:
        cur = queue.pop()
        for row in conn.execute(
            "SELECT id FROM tasks WHERE parent_id = ?", (cur,)
        ).fetchall():
            if row["id"] not in seen:  # seen makes even a corrupted cycle terminate
                seen.add(row["id"])
                out.append(row["id"])
                queue.append(row["id"])
    return out


def subtask_stats(conn: sqlite3.Connection, task_id: int) -> dict:
    """Rollup over ALL descendants (Notion-style): {total, done, pct}."""
    ids = descendants(conn, task_id)
    if not ids:
        return {"total": 0, "done": 0, "pct": 0}
    marks = ",".join("?" for _ in ids)
    done = conn.execute(
        f"SELECT COUNT(*) AS n FROM tasks WHERE id IN ({marks}) AND status = 'done'",
        ids,
    ).fetchone()["n"]
    return {"total": len(ids), "done": done, "pct": round(100 * done / len(ids))}


def tree(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    status: str | list[str] | None = None,
    include_cancelled: bool = True,
    q: str | None = None,
) -> list[dict]:
    """Nested task hierarchy: root tasks first, each with "children" (recursive)
    and "progress" (subtask rollup). Same filters as list()."""
    flat = list(conn, project_id=project_id, status=status,
                include_cancelled=include_cancelled, q=q)
    in_set = {t.id for t in flat}
    by_parent: dict[int | None, list[Task]] = {}
    roots: list[Task] = []
    for t in flat:
        # a task whose parent is filtered out is promoted to the root level
        # (Notion-like: it stays visible instead of vanishing with its parent)
        if t.parent_id is None or t.parent_id not in in_set:
            roots.append(t)
        else:
            by_parent.setdefault(t.parent_id, []).append(t)

    # Rollup progress for EVERY node in ONE recursive CTE (all ancestor->descendant
    # pairs with the descendant's status) instead of one descendants()+COUNT query
    # per node (was O(N^2) queries). UNION (not UNION ALL) dedupes pairs, so even a
    # corrupted parent cycle terminates. Same semantics as subtask_stats().
    pairs = conn.execute(
        "WITH RECURSIVE rel(anc, desc) AS ("
        "  SELECT parent_id, id FROM tasks WHERE parent_id IS NOT NULL"
        "  UNION"
        "  SELECT r.anc, t.id FROM rel r JOIN tasks t ON t.parent_id = r.desc"
        ") SELECT r.anc AS anc, t.status AS status FROM rel r JOIN tasks t ON t.id = r.desc"
    ).fetchall()
    tot: dict[int, int] = {}
    done: dict[int, int] = {}
    for r in pairs:
        anc = r["anc"]
        tot[anc] = tot.get(anc, 0) + 1
        if r["status"] == "done":
            done[anc] = done.get(anc, 0) + 1

    def progress(tid: int) -> dict:
        t_, d_ = tot.get(tid, 0), done.get(tid, 0)
        return {"total": t_, "done": d_, "pct": round(100 * d_ / t_) if t_ else 0}

    def build(t: Task) -> dict:
        node = t.to_dict()
        node["children"] = [build(k) for k in by_parent.get(t.id, [])]
        node["progress"] = progress(t.id)
        return node

    return [build(t) for t in roots]
