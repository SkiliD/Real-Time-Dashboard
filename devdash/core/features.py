# features = named groups of tasks with a progress bar
# Works on Windows, Mac and Linux.

from __future__ import annotations

import sqlite3

from . import events, i18n, projects
from . import tasks as tsk
from .models import Feature, check_text, now_iso

VALID_STATUSES = ("open", "proposed", "done", "rejected")


def create(conn: sqlite3.Connection, project_id: int, name: str, description: str = "") -> Feature:
    name = name.strip()
    if not name:
        raise ValueError(i18n.tr("err.feature_name_empty"))
    description = check_text(description or "", "description")
    if projects.get(conn, project_id) is None:
        raise KeyError(i18n.tr("err.no_project_id", id=project_id))
    with conn:  # row + event in one transaction
        cur = conn.execute(
            "INSERT INTO features(project_id, name, description, status, source, created_at) "
            "VALUES (?, ?, ?, 'open', 'manual', ?)",
            (project_id, name, description, now_iso()),
        )
        f = get(conn, cur.lastrowid)
        assert f is not None
        events.append(conn, "feature", f.id, "created", f.to_dict(), commit=False)
    return f


def get(conn: sqlite3.Connection, ref: int | str, project_id: int | None = None) -> Feature | None:
    """By id (int) or by name. Names are ALWAYS scoped to a project (the given
    one, else the active one) — never resolved across projects, so a name can
    never mutate another project's feature by accident. With no project at all,
    a global name lookup is allowed only when exactly one feature matches."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        row = conn.execute("SELECT * FROM features WHERE id = ?", (int(ref),)).fetchone()
        return Feature.from_row(row) if row else None
    pid = project_id
    if pid is None:
        active = projects.get_active(conn)
        pid = active.id if active else None
    if pid is not None:
        row = conn.execute(
            "SELECT * FROM features WHERE name = ? AND project_id = ?", (ref, pid)
        ).fetchone()
        return Feature.from_row(row) if row else None
    rows = conn.execute("SELECT * FROM features WHERE name = ?", (ref,)).fetchall()
    if len(rows) == 1:
        return Feature.from_row(rows[0])
    return None  # 0 matches, or ambiguous across projects -> caller raises KeyError


def list(conn: sqlite3.Connection, project_id: int | None = None, status: str | None = None,
         trash: bool = False) -> list[dict]:
    """Features with task progress counts (single grouped query, no N+1).
    Soft-deleted features are excluded unless trash=True."""
    clauses, values = [], []
    clauses.append("deleted_at IS NOT NULL" if trash else "deleted_at IS NULL")
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    if status is not None:
        clauses.append("status = ?")
        values.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM features {where} ORDER BY created_at DESC, id DESC", values
    ).fetchall()
    counts = {
        r["feature_id"]: (r["total"], r["done"])
        for r in conn.execute(
            "SELECT feature_id, COUNT(*) AS total, "
            "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done "
            "FROM tasks WHERE feature_id IS NOT NULL AND status != 'cancelled' "
            "AND deleted_at IS NULL "  # soft-deleted tasks must not count (trash)
            "GROUP BY feature_id"
        ).fetchall()
    }
    out = []
    for r in rows:
        f = Feature.from_row(r)
        total, done = counts.get(f.id, (0, 0))
        pct = round(100 * done / total, 1) if total else 0.0
        out.append({**f.to_dict(), "progress": {"total": total, "done": done, "pct": pct}})
    return out


def attach(conn: sqlite3.Connection, ref: int | str, task_id: int, project_id: int | None = None) -> Feature:
    f = get(conn, ref, project_id)
    if f is None:
        raise KeyError(i18n.tr("err.no_feature", ref=ref))
    t = tsk.get(conn, task_id)
    if t is None:
        raise KeyError(i18n.tr("err.no_task", id=task_id))
    if t.project_id != f.project_id:
        raise ValueError(
            f"task #{task_id} belongs to project #{t.project_id}, "
            f"feature {f.name!r} belongs to project #{f.project_id}"
        )
    tsk.update(conn, task_id, feature_id=f.id)
    events.append(conn, "feature", f.id, "task_attached", {"task_id": task_id})
    return f


def detach(conn: sqlite3.Connection, ref: int | str, task_id: int, project_id: int | None = None) -> Feature:
    f = get(conn, ref, project_id)
    if f is None:
        raise KeyError(i18n.tr("err.no_feature", ref=ref))
    t = tsk.get(conn, task_id)
    if t is not None and t.feature_id == f.id:
        tsk.update(conn, task_id, feature_id=None)
    events.append(conn, "feature", f.id, "task_detached", {"task_id": task_id})
    return f


def progress(conn: sqlite3.Connection, feature_id: int, include_tasks: bool = True) -> dict:
    rows = tsk.list(conn, feature_id=feature_id, include_cancelled=False)
    done = [t for t in rows if t.status == "done"]
    p = {
        "total": len(rows),
        "done": len(done),
        "pct": round(100 * len(done) / len(rows), 1) if rows else 0.0,
    }
    if include_tasks:
        p["tasks"] = [t.to_dict() for t in rows]
    return p


def check_completion(conn: sqlite3.Connection, feature_id: int) -> dict:
    """SIGNAL ONLY — never changes status. True when the feature has tasks and
    all of them are done (plus optionally linked commits, checked by caller)."""
    p = progress(conn, feature_id)
    return {"all_done": p["total"] > 0 and p["done"] == p["total"], **p}


def propose(
    conn: sqlite3.Connection,
    ref: int | str,
    project_id: int | None = None,
    *,
    source: str = "ai",
    confidence: float | None = None,
    reasoning: str | None = None,
) -> Feature:
    """Record a proposal (e.g. from the AI) — status becomes 'proposed', NOT done."""
    f = get(conn, ref, project_id)
    if f is None:
        raise KeyError(i18n.tr("err.no_feature", ref=ref))
    if f.status == "done":
        raise ValueError(i18n.tr("err.feature_already_done", name=f.name))
    with conn:
        conn.execute(
            "UPDATE features SET status = 'proposed', source = ?, confidence = ?, reasoning = ? WHERE id = ?",
            (source, confidence, reasoning, f.id),
        )
        f = get(conn, f.id)
        events.append(conn, "feature", f.id, "proposed", f.to_dict(), commit=False)
    return f


def approve(conn: sqlite3.Connection, ref: int | str, project_id: int | None = None, note: str = "") -> Feature:
    """HUMAN DECISION — the only path to status='done'. Sets validated_at."""
    f = get(conn, ref, project_id)
    if f is None:
        raise KeyError(i18n.tr("err.no_feature", ref=ref))
    if f.status == "done":
        raise ValueError(i18n.tr("err.feature_already_done", name=f.name))
    now = now_iso()
    with conn:
        conn.execute(
            "UPDATE features SET status = 'done', validated_at = ?, feedback = ? WHERE id = ?",
            (now, note or None, f.id),
        )
        f = get(conn, f.id)
        events.append(conn, "feature", f.id, "validated",
                      {**f.to_dict(), "by": "human", "note": note}, commit=False)
    return f


def reject(conn: sqlite3.Connection, ref: int | str, feedback: str = "", project_id: int | None = None) -> Feature:
    """Human rejection of a proposal — stored as feedback, never re-proposed blindly."""
    f = get(conn, ref, project_id)
    if f is None:
        raise KeyError(i18n.tr("err.no_feature", ref=ref))
    with conn:
        conn.execute(
            "UPDATE features SET status = 'rejected', feedback = ? WHERE id = ?",
            (feedback, f.id),
        )
        f = get(conn, f.id)
        events.append(conn, "feature", f.id, "rejected", {**f.to_dict(), "feedback": feedback}, commit=False)
    return f


def reopen(conn: sqlite3.Connection, ref: int | str, project_id: int | None = None) -> Feature:
    f = get(conn, ref, project_id)
    if f is None:
        raise KeyError(i18n.tr("err.no_feature", ref=ref))
    with conn:
        conn.execute(
            "UPDATE features SET status = 'open', validated_at = NULL, confidence = NULL, reasoning = NULL WHERE id = ?",
            (f.id,),
        )
        f = get(conn, f.id)
        events.append(conn, "feature", f.id, "reopened", f.to_dict(), commit=False)
    return f


def update(
    conn: sqlite3.Connection,
    ref: int | str,
    project_id: int | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Feature:
    """Rename / edit a feature. Names stay unique within a project."""
    f = get(conn, ref, project_id)
    if f is None:
        raise KeyError(i18n.tr("err.no_feature", ref=ref))
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError(i18n.tr("err.feature_name_empty"))
        dup = conn.execute(
            "SELECT id FROM features WHERE project_id = ? AND name = ? AND id != ?",
            (f.project_id, name, f.id),
        ).fetchone()
        if dup:
            raise ValueError(i18n.tr("err.feature_exists", name=name))
    fields, values = [], []
    if name is not None:
        fields.append("name = ?")
        values.append(name)
    if description is not None:
        description = check_text(description, "description")
        fields.append("description = ?")
        values.append(description)
    if fields:
        values.append(f.id)
        with conn:
            conn.execute(f"UPDATE features SET {', '.join(fields)} WHERE id = ?", values)
            f = get(conn, f.id)
            events.append(conn, "feature", f.id, "updated", f.to_dict(), commit=False)
    return f


def delete(conn: sqlite3.Connection, ref: int | str, project_id: int | None = None,
           permanent: bool = False) -> Feature:
    """Delete a feature. Default = SOFT delete (corbeille, restorable via
    restore()); permanent=True = hard delete: linked tasks keep their rows but
    lose feature_id (SET NULL), and the feature's events are cleaned up."""
    f = get(conn, ref, project_id)
    if f is None:
        raise KeyError(i18n.tr("err.no_feature", ref=ref))
    if permanent:
        with conn:
            conn.execute("DELETE FROM events WHERE entity = 'feature' AND entity_id = ?", (f.id,))
            conn.execute("UPDATE tasks SET feature_id = NULL WHERE feature_id = ?", (f.id,))
            conn.execute("DELETE FROM features WHERE id = ?", (f.id,))
        events.append(conn, "feature", f.id, "deleted", {"id": f.id, "name": f.name, "permanent": True})
        return f
    with conn:
        conn.execute("UPDATE features SET deleted_at = ? WHERE id = ?", (now_iso(), f.id))
    events.append(conn, "feature", f.id, "deleted", {"id": f.id, "name": f.name, "soft": True})
    return f


def restore(conn: sqlite3.Connection, ref: int | str, project_id: int | None = None) -> Feature:
    """Restore a soft-deleted feature."""
    f = get(conn, ref, project_id)
    if f is None:
        raise KeyError(i18n.tr("err.no_feature", ref=ref))
    with conn:
        conn.execute("UPDATE features SET deleted_at = NULL WHERE id = ?", (f.id,))
    events.append(conn, "feature", f.id, "restored", {"id": f.id, "name": f.name})
    return get(conn, ref, project_id)
