# numbers for the charts: per-day time, streaks, tags

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import sessions
from . import tasks as tsk
from .models import day_start_utc, local_now, parse_tags, to_local_date, today_str_local

DEFAULT_WEIGHT_MIN = 60  # tasks without estimate weigh this much


def today_str() -> str:
    """Today's date in the LOCAL timezone (YYYY-MM-DD)."""
    return today_str_local()




def _cutoff(days: int) -> str:
    """Start of the local day `days-1` days ago, as a UTC ISO string."""
    return day_start_utc(days - 1)


def _local_days(days: int) -> list[str]:
    """The last `days` local dates (YYYY-MM-DD), oldest first."""
    now = local_now()
    return [(now - timedelta(days=i)).date().isoformat() for i in range(days - 1, -1, -1)]


def time_by_day(conn: sqlite3.Connection, project_id: int | None = None, days: int = 14) -> list[dict]:
    """Minutes worked per LOCAL day (stopped sessions), oldest first, gaps filled with 0."""
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
        day = to_local_date(r["started_at"])
        by_day[day] = by_day.get(day, 0.0) + (r["duration_min"] or 0.0)
    return [
        {"date": d, "minutes": round(by_day.get(d, 0.0), 2)}
        for d in _local_days(days)
    ]


def today_minutes(conn: sqlite3.Connection, project_id: int | None = None) -> float:
    """Minutes today = stopped sessions today + live elapsed of the running one."""
    total = sessions.total_min(conn, project_id=project_id, today=True)
    running = sessions.current(conn)
    if running is not None and (project_id is None or running.project_id == project_id):
        total += sessions.elapsed_min(running)
    return round(total, 2)


def tasks_done_today(conn: sqlite3.Connection, project_id: int | None = None) -> int:
    """Tasks completed today (LOCAL day), as one SQL COUNT.

    Same boundary as status.snapshot(): completed_at >= start of today local.
    """
    clauses = ["status = 'done'", "completed_at IS NOT NULL", "completed_at >= ?"]
    values: list = [day_start_utc(0)]
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM tasks WHERE {' AND '.join(clauses)}", values
    ).fetchone()
    return int(row["n"])


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
        day = to_local_date(r["completed_at"])
        by_day[day] = by_day.get(day, 0) + 1
    return [
        {"date": d, "count": by_day.get(d, 0)}
        for d in _local_days(days)
    ]


def streak_days(conn: sqlite3.Connection, project_id: int | None = None) -> int:
    """Consecutive LOCAL days (ending today or yesterday) with at least one stopped session."""
    days = sessions.active_days(conn, project_id)
    today = local_now().date()
    cursor = today if today.isoformat() in days else today - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def project_progress(conn: sqlite3.Connection, project_id: int, all_tasks: list | None = None) -> dict:
    """Progress %: raw task count and estimate-weighted (tasks without estimate
    weigh DEFAULT_WEIGHT_MIN). `all_tasks` lets the caller reuse a list it
    already loaded (the GUI /api/stats loads tasks anyway) instead of re-querying."""
    if all_tasks is None:
        all_tasks = tsk.list(conn, project_id=project_id, include_cancelled=False)
    done = [t for t in all_tasks if t.status == "done"]

    def weight(t) -> float:
        # an explicit 0-minute estimate is a real 0, not "no estimate"
        return float(t.estimate_min) if t.estimate_min is not None else float(DEFAULT_WEIGHT_MIN)

    w_total = sum(weight(t) for t in all_tasks)
    w_done = sum(weight(t) for t in done)
    return {
        "total": len(all_tasks),
        "done": len(done),
        "pct_tasks": round(100 * len(done) / len(all_tasks), 1) if all_tasks else 0.0,
        "pct_weighted": round(100 * w_done / w_total, 1) if w_total else 0.0,
    }


def period_minutes(conn: sqlite3.Connection, project_id: int | None = None, days: int = 7) -> float:
    """Total minutes of stopped sessions over the last `days` LOCAL days.

    Uses the same day boundary as time_by_day() so the period total always
    equals the sum of the per-day buckets (no rolling-UTC drift).
    """
    cutoff = _cutoff(days)
    clauses = ["ended_at IS NOT NULL", "started_at >= ?"]
    values: list = [cutoff]
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    rows = conn.execute(
        f"SELECT duration_min FROM sessions WHERE {' AND '.join(clauses)}", values
    ).fetchall()
    return round(sum(r["duration_min"] or 0.0 for r in rows), 2)


def regroup_rows(rows: list[dict], group: str, value_key: str) -> list[dict]:
    """Re-bucket daily rows [{date: 'YYYY-MM-DD', <value_key>: n}] into
    weeks ('YYYY-Www') or months ('YYYY-MM'). Day = unchanged. Labels sorted."""
    if group not in ("week", "month"):
        return rows
    buckets: dict[str, float] = {}
    for r in rows:
        d = r["date"]
        if group == "month":
            label = d[:7]
        else:
            iso = date.fromisoformat(d).isocalendar()
            label = f"{iso[0]}-W{iso[1]:02d}"
        buckets[label] = buckets.get(label, 0.0) + (r.get(value_key) or 0)
    return [{"date": k, value_key: round(v, 2)} for k, v in sorted(buckets.items())]


def minutes_by_project(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    """Minutes worked per project over the last `days` days (all projects),
    most active first. Used by the GUI stats 'all projects' breakdown."""
    cutoff = _cutoff(days)
    rows = conn.execute(
        "SELECT p.id, p.slug, p.name, p.color, "
        "COALESCE(SUM(s.duration_min), 0) AS minutes "
        "FROM projects p LEFT JOIN sessions s "
        "  ON s.project_id = p.id AND s.ended_at IS NOT NULL AND s.started_at >= ? "
        "WHERE p.archived = 0 GROUP BY p.id ORDER BY minutes DESC",
        (cutoff,),
    ).fetchall()
    return [
        {"project": r["id"], "slug": r["slug"], "name": r["name"],
         "color": r["color"], "minutes": round(r["minutes"] or 0.0, 2)}
        for r in rows
    ]


def estimates(conn: sqlite3.Connection, project_id: int | None = None) -> dict:
    """Estimate vs actual per task: tasks.estimate_min against the SUM of the
    stopped sessions attached to the task (the running session is excluded, so
    the numbers are deterministic). The aggregate only covers tasks that HAVE
    an explicit estimate; tasks with actual time but no estimate still appear
    in the per-task rows (an unestimated overrun is invisible otherwise).
    One GROUP BY query instead of one SUM per task (was N+1 on every poll)."""
    tasks = tsk.list(conn, project_id=project_id, include_cancelled=False)
    # single aggregate over all listed tasks (idx_sessions_task), then joined in Python
    agg: dict[int, tuple[float, int]] = {}
    if tasks:
        marks = ",".join("?" for _ in tasks)
        for r in conn.execute(
            f"SELECT task_id, COALESCE(SUM(duration_min), 0) AS m, COUNT(*) AS n "
            f"FROM sessions WHERE ended_at IS NOT NULL AND task_id IN ({marks}) "
            f"GROUP BY task_id",
            [t.id for t in tasks],
        ).fetchall():
            agg[r["task_id"]] = (float(r["m"] or 0.0), int(r["n"]))
    rows = []
    for t in tasks:
        minutes, n_sessions = agg.get(t.id, (0.0, 0))
        actual = round(minutes, 2)
        est = t.estimate_min
        rows.append({
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "estimate_min": est,
            "actual_min": actual,
            "sessions": n_sessions,
            "delta_min": round(actual - est, 2) if est is not None else None,
            # est=0 is a real zero but a ratio would divide by zero -> None
            "ratio": round(actual / est, 3) if est else None,
        })
    est_rows = [r for r in rows if r["estimate_min"] is not None]
    est_total = sum(r["estimate_min"] for r in est_rows)
    act_on_est = round(sum(r["actual_min"] for r in est_rows), 2)
    over = [r for r in est_rows if r["estimate_min"] > 0 and r["actual_min"] > r["estimate_min"] * 1.2]
    under = [r for r in est_rows if r["estimate_min"] > 0 and 0 < r["actual_min"] < r["estimate_min"] * 0.8]
    agg_out = {
        "tasks": len(rows),
        "with_estimate": len(est_rows),
        "est_total_min": est_total,
        "actual_on_estimated_min": act_on_est,
        "pct_of_estimate": round(100.0 * act_on_est / est_total, 1) if est_total else None,
        "over": len(over),
        "under": len(under),
        "actual_total_min": round(sum(r["actual_min"] for r in rows), 2),
    }
    # biggest overruns first (ratio desc); tasks without estimate go last
    rows.sort(key=lambda r: (r["ratio"] is None, -(r["ratio"] or 0.0)))
    return {"tasks": rows, "agg": agg_out}


def by_tag(conn: sqlite3.Connection, project_id: int | None = None, days: int | None = None) -> list[dict]:
    """Per-tag stats: number of (non-cancelled) tasks and minutes worked on
    tagged tasks (via sessions attached to those tasks). `days` bounds the
    session scan to the recent window (the GUI stats tab passes its period
    filter; without it every session ever is read)."""
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
        for tag in parse_tags(r["tags"] or "[]"):  # corrupt JSON -> [] inside parse_tags
            tasks_per_tag[tag] = tasks_per_tag.get(tag, 0) + 1
    # minutes per tag: sessions joined to their task's tags
    clauses2 = ["s.ended_at IS NOT NULL", "s.task_id IS NOT NULL"]
    values2: list = []
    if days is not None:  # bound the scan (served by idx_sessions_started)
        clauses2.append("s.started_at >= ?")
        values2.append(_cutoff(days))
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
        for tag in parse_tags(r["tags"] or "[]"):
            min_per_tag[tag] = min_per_tag.get(tag, 0.0) + (r["dur"] or 0.0)
    tags = set(tasks_per_tag) | set(min_per_tag)
    return [
        {"tag": t, "tasks": tasks_per_tag.get(t, 0),
         "minutes": round(min_per_tag.get(t, 0.0), 2)}
        for t in sorted(tags, key=lambda t: (tasks_per_tag.get(t, 0), min_per_tag.get(t, 0.0)), reverse=True)
    ]
