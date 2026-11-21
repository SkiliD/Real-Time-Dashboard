"""Productivity statistics (core). Used by `devdash status` now and by the
GUI charts in step (j). All times are UTC ISO strings.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import sessions
from . import tasks as tsk

DEFAULT_WEIGHT_MIN = 60  # tasks without estimate weigh this much


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _cutoff(days: int) -> str:
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")


def time_by_day(conn: sqlite3.Connection, project_id: int | None = None, days: int = 14) -> list[dict]:
    """Minutes worked per day (stopped sessions), oldest first, gaps filled with 0."""
    clauses = ["ended_at IS NOT NULL", "started_at >= ?"]
    values: list = [_cutoff(days)]
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    rows = conn.execute(
        f"SELECT started_at, duration_min FROM sessions WHERE {' AND '.join(clauses)}",
        values,
    ).fetchall()
    by_day: dict[str, float] = {}
    for r in rows:
        day = r["started_at"][:10]
        by_day[day] = by_day.get(day, 0.0) + (r["duration_min"] or 0.0)
    now = datetime.now(timezone.utc)
    return [
        {"date": (now - timedelta(days=i)).date().isoformat(),
         "minutes": round(by_day.get((now - timedelta(days=i)).date().isoformat(), 0.0), 2)}
        for i in range(days - 1, -1, -1)
    ]


def today_minutes(conn: sqlite3.Connection, project_id: int | None = None) -> float:
    """Minutes today = stopped sessions today + live elapsed of the running one."""
    total = sessions.total_min(conn, project_id=project_id, today=True)
    running = sessions.current(conn)
    if running is not None and (project_id is None or running.project_id == project_id):
        total += sessions.elapsed_min(running)
    return round(total, 2)


def tasks_done_by_day(conn: sqlite3.Connection, project_id: int | None = None, days: int = 7) -> list[dict]:
    """Tasks completed per day, oldest first, gaps filled with 0."""
    clauses = ["status = 'done'", "completed_at IS NOT NULL", "completed_at >= ?"]
    values: list = [_cutoff(days)]
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    rows = conn.execute(
        f"SELECT completed_at FROM tasks WHERE {' AND '.join(clauses)}", values
    ).fetchall()
    by_day: dict[str, int] = {}
    for r in rows:
        day = r["completed_at"][:10]
        by_day[day] = by_day.get(day, 0) + 1
    now = datetime.now(timezone.utc)
    return [
        {"date": (now - timedelta(days=i)).date().isoformat(),
         "count": by_day.get((now - timedelta(days=i)).date().isoformat(), 0)}
        for i in range(days - 1, -1, -1)
    ]


def streak_days(conn: sqlite3.Connection, project_id: int | None = None) -> int:
    """Consecutive days (ending today or yesterday) with at least one stopped session."""
    clauses = ["ended_at IS NOT NULL"]
    values: list = []
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    rows = conn.execute(
        f"SELECT DISTINCT substr(started_at, 1, 10) AS d FROM sessions WHERE {' AND '.join(clauses)}",
        values,
    ).fetchall()
    days = {r["d"] for r in rows}
    today = datetime.now(timezone.utc).date()
    cursor = today if today.isoformat() in days else today - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def project_progress(conn: sqlite3.Connection, project_id: int) -> dict:
    """Progress %: raw task count and estimate-weighted (tasks without estimate
    weigh DEFAULT_WEIGHT_MIN)."""
    all_tasks = tsk.list(conn, project_id=project_id, include_cancelled=False)
    done = [t for t in all_tasks if t.status == "done"]

    def weight(t) -> float:
        return float(t.estimate_min or DEFAULT_WEIGHT_MIN)

    w_total = sum(weight(t) for t in all_tasks)
    w_done = sum(weight(t) for t in done)
    return {
        "total": len(all_tasks),
        "done": len(done),
        "pct_tasks": round(100 * len(done) / len(all_tasks), 1) if all_tasks else 0.0,
        "pct_weighted": round(100 * w_done / w_total, 1) if w_total else 0.0,
    }


def period_minutes(conn: sqlite3.Connection, project_id: int | None = None, days: int = 7) -> float:
    """Total minutes of stopped sessions over the last `days` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    clauses = ["ended_at IS NOT NULL", "started_at >= ?"]
    values: list = [cutoff]
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    rows = conn.execute(
        f"SELECT duration_min FROM sessions WHERE {' AND '.join(clauses)}", values
    ).fetchall()
    return round(sum(r["duration_min"] or 0.0 for r in rows), 2)


def by_tag(conn: sqlite3.Connection, project_id: int | None = None) -> list[dict]:
    """Per-tag stats: number of (non-cancelled) tasks and minutes worked on
    tagged tasks (via sessions attached to those tasks)."""
    import json as _json

    clauses = ["status != 'cancelled'"]
    values: list = []
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    rows = conn.execute(
        f"SELECT tags FROM tasks WHERE {' AND '.join(clauses)}", values
    ).fetchall()
    tasks_per_tag: dict[str, int] = {}
    for r in rows:
        try:
            for tag in _json.loads(r["tags"] or "[]"):
                tasks_per_tag[tag] = tasks_per_tag.get(tag, 0) + 1
        except _json.JSONDecodeError:
            pass
    # minutes per tag: sessions joined to their task's tags
    clauses2 = ["s.ended_at IS NOT NULL", "s.task_id IS NOT NULL"]
    values2: list = []
    if project_id is not None:
        clauses2.append("s.project_id = ?")
        values2.append(project_id)
    rows2 = conn.execute(
        f"SELECT t.tags AS tags, s.duration_min AS dur FROM sessions s "
        f"JOIN tasks t ON s.task_id = t.id WHERE {' AND '.join(clauses2)}",
        values2,
    ).fetchall()
    min_per_tag: dict[str, float] = {}
    for r in rows2:
        try:
            for tag in _json.loads(r["tags"] or "[]"):
                min_per_tag[tag] = min_per_tag.get(tag, 0.0) + (r["dur"] or 0.0)
        except _json.JSONDecodeError:
            pass
    tags = set(tasks_per_tag) | set(min_per_tag)
    return [
        {"tag": t, "tasks": tasks_per_tag.get(t, 0),
         "minutes": round(min_per_tag.get(t, 0.0), 2)}
        for t in sorted(tags, key=lambda t: (tasks_per_tag.get(t, 0), min_per_tag.get(t, 0.0)), reverse=True)
    ]
