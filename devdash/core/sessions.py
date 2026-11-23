"""work sessions and durations

If it breaks, the tests will say so.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import events, projects
from .models import Session, day_start_utc, now_iso, to_local_date

VALID_KINDS = ("focus", "pause", "custom")

# hard cap so a runaway query can never materialize the whole table in RAM
MAX_LIST_ROWS = 100_000


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _period_clauses(today: bool, week: bool) -> tuple[list[str], list]:
    """Shared today/week filters. Day bounds are LOCAL midnight converted to UTC."""
    clauses, values = [], []
    if today:
        clauses.append("started_at >= ?")
        values.append(day_start_utc(0))
    if week:
        clauses.append("started_at >= ?")
        values.append(day_start_utc(7))
    return clauses, values


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
    if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int) or not (1 <= minutes <= 24 * 60)):
        raise ValueError("minutes must be an integer between 1 and 1440")
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
    # Atomic: stop ANY still-running sessions (normally at most one) and insert
    # the new one in a single IMMEDIATE transaction — BEGIN IMMEDIATE takes the
    # write lock up front, so the SELECT + INSERT are truly serialized against
    # other writers (CLI/GUI/watcher) and a crash can never leave two running.
    # Auto-stopped sessions emit their own "stopped" event so live GUIs see it.
    try:
        conn.execute("BEGIN IMMEDIATE")
        stale = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL"
        ).fetchall()
        conn.execute(
            "UPDATE sessions SET ended_at = ?, "
            "duration_min = MAX(0, ROUND((julianday(?) - julianday(started_at)) * 24 * 60, 2)) "
            "WHERE ended_at IS NULL",
            (now, now),
        )
        for row in stale:
            events.append(conn, "session", row["id"], "stopped", None, commit=False)
        cur = conn.execute(
            "INSERT INTO sessions(project_id, task_id, kind, started_at, ended_at, "
            "duration_min, planned_min, note) VALUES (?, ?, ?, ?, NULL, NULL, ?, '')",
            (project_id, task_id, kind, now, minutes),
        )
        session_id = cur.lastrowid
        # NOTE: the "started" event is emitted ONCE, after the commit, with the
        # full payload (a second bare event inside the transaction would make
        # pollers see two "started" for one session).
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    session = get(conn, session_id)
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
    duration = max(0.0, round((_parse(ended) - _parse(session.started_at)).total_seconds() / 60, 2))
    # Idempotent stop: the WHERE ended_at IS NULL guard makes a concurrent second
    # stop a no-op instead of overwriting ended_at/duration/note.
    cur = conn.execute(
        "UPDATE sessions SET ended_at = ?, duration_min = ?, note = ? "
        "WHERE id = ? AND ended_at IS NULL",
        (ended, duration, note or session.note, session_id),
    )
    if cur.rowcount == 0:
        raise ValueError(f"session #{session_id} is already stopped")
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
    """Session history, newest first. today/week use local-day boundaries (stored in UTC)."""
    clauses, values = [], []
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    if task_id is not None:
        clauses.append("task_id = ?")
        values.append(task_id)
    p_clauses, p_values = _period_clauses(today, week)
    clauses += p_clauses
    values += p_values
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = min(limit, MAX_LIST_ROWS)
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
    """SUM in SQL of stopped sessions (the running one is excluded)."""
    clauses = ["ended_at IS NOT NULL"]
    values: list = []
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    p_clauses, p_values = _period_clauses(today, week)
    clauses += p_clauses
    values += p_values
    where = f"WHERE {' AND '.join(clauses)}"
    row = conn.execute(
        f"SELECT COALESCE(SUM(duration_min), 0) AS s FROM sessions {where}", values
    ).fetchone()
    return round(float(row["s"]), 2)


def active_days(conn: sqlite3.Connection, project_id: int | None = None) -> set[str]:
    """Set of LOCAL calendar dates (YYYY-MM-DD) with at least one stopped session."""
    clauses = ["ended_at IS NOT NULL"]
    values: list = []
    if project_id is not None:
        clauses.append("project_id = ?")
        values.append(project_id)
    rows = conn.execute(
        f"SELECT DISTINCT started_at FROM sessions WHERE {' AND '.join(clauses)}", values
    ).fetchall()
    return {to_local_date(r["started_at"]) for r in rows}
