"""Work sessions + Pomodoro support (core).

A session is attached to a project and optionally to a task. Only ONE session
runs at a time: starting a new one auto-stops the running one (recorded with
an empty note), which keeps the timeline clean for a solo developer.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import events, projects
from .models import Session, now_iso

VALID_KINDS = ("focus", "pause", "custom")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def get(conn: sqlite3.Connection, session_id: int) -> Session | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return Session.from_row(row) if row else None


def current(conn: sqlite3.Connection) -> Session | None:
    """The running session (ended_at IS NULL), if any."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE ended_at IS NULL "
        "ORDER BY started_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return Session.from_row(row) if row else None


def start(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    task_id: int | None = None,
    kind: str = "focus",
    minutes: int | None = None,
) -> Session:
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind {kind!r}, expected one of {VALID_KINDS}")
    running = current(conn)
    if running is not None:
        stop(conn, running.id, note="")  # auto-stop the previous session
    if project_id is None:
        active = projects.get_active(conn)
        if active is None:
            raise KeyError("no active project (set one with 'devdash projects set-active')")
        project_id = active.id
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO sessions(project_id, task_id, kind, started_at, ended_at, "
        "duration_min, planned_min, note) VALUES (?, ?, ?, ?, NULL, NULL, ?, '')",
        (project_id, task_id, kind, now, minutes),
    )
    conn.commit()
    session = get(conn, cur.lastrowid)
    assert session is not None
    events.append(conn, "session", session.id, "started", session.to_dict())
    return session


def stop(conn: sqlite3.Connection, session_id: int | None = None, note: str = "") -> Session:
    """Stop a session (default: the running one) and record duration + note."""
    if session_id is None:
        running = current(conn)
        if running is None:
            raise KeyError("no running session")
        session_id = running.id
    session = get(conn, session_id)
    if session is None:
        raise KeyError(f"no session #{session_id}")
    if session.ended_at is not None:
        raise ValueError(f"session #{session_id} is already stopped")
    ended = now_iso()
    duration = round((_parse(ended) - _parse(session.started_at)).total_seconds() / 60, 2)
    conn.execute(
        "UPDATE sessions SET ended_at = ?, duration_min = ?, note = ? WHERE id = ?",
        (ended, duration, note or session.note, session_id),
    )
    conn.commit()
    session = get(conn, session_id)
    assert session is not None
    events.append(conn, "session", session.id, "stopped", session.to_dict())
    return session


def elapsed_min(session: Session, now: datetime | None = None) -> float:
    """Elapsed minutes: stored duration if stopped, live duration if running."""
    if session.ended_at:
        return session.duration_min or 0.0
    end = now or datetime.now(timezone.utc)
    return round((end - _parse(session.started_at)).total_seconds() / 60, 2)


def list(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    task_id: int | None = None,
    today: bool = False,
    week: bool = False,
    limit: int = 100,
) -> list[Session]:
    """Session history, newest first. today/week filter on started_at (UTC)."""
    clauses, values = [], []
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    if task_id is not None:
        clauses.append("task_id = ?")
        values.append(task_id)
    now = datetime.now(timezone.utc)
    if today:
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        clauses.append("started_at >= ?")
        values.append(cutoff)
    if week:
        cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds")
        clauses.append("started_at >= ?")
        values.append(cutoff)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM sessions {where} ORDER BY started_at DESC, id DESC LIMIT ?",
        (*values, limit),
    ).fetchall()
    return [Session.from_row(r) for r in rows]


def total_min(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    today: bool = False,
    week: bool = False,
) -> float:
    """Sum of durations (stopped sessions only; the running one is excluded)."""
    sessions = list(conn, project_id=project_id, today=today, week=week, limit=100000)
    return round(sum(s.duration_min or 0.0 for s in sessions if s.ended_at), 2)
