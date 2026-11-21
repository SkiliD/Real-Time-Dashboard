"""Instant snapshot of a project — the brain of `devdash status` (CLI) and of
the GUI header. Pure read over the other core modules.
"""

from __future__ import annotations

import sqlite3

from . import events, gitwatch, projects, sessions, stats
from . import tasks as tsk
from .models import day_start_utc, now_iso


def snapshot(conn: sqlite3.Connection, project_id: int | None = None) -> dict:
    """Full instant picture. project_id=None -> the active project."""
    if project_id is None:
        active = projects.get_active(conn)
        pid = active.id if active else None
    else:
        pid = project_id
        active = projects.get(conn, pid)
    data: dict = {
        "generated_at": now_iso(),
        "last_event_id": events.last_id(conn),
        "project": active.to_dict() if active else None,
    }
    if active is None:
        return data

    open_tasks = tsk.list(conn, project_id=pid, status=["todo", "in_progress"])
    # one COUNT query instead of loading every done task
    done_today = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE project_id = ? AND status = 'done' "
        "AND completed_at >= ?",
        (pid, day_start_utc(0)),
    ).fetchone()["c"]
    s = sessions.current(conn)
    if s is not None and s.project_id != pid:
        s = None  # running session belongs to another project
    data.update({
        "tasks": {
            "open": len(open_tasks),
            "in_progress": [t.to_dict() for t in open_tasks if t.status == "in_progress"],
            "done_today": done_today,
        },
        "session": None if s is None else {**s.to_dict(), "elapsed_min": sessions.elapsed_min(s)},
        "today_minutes": stats.today_minutes(conn, pid),
        "streak_days": stats.streak_days(conn, pid),
        "progress": stats.project_progress(conn, pid),
        "git": gitwatch.status(conn, pid),
    })
    return data
