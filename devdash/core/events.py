"""Append-only event bus used for realtime updates.

Every core mutation calls `append(...)`. The GUI polls `since(last_id)` to
refresh without restart; the CLI can watch it with `devdash status --watch`.
"""

from __future__ import annotations

import json
import sqlite3

from .models import Event, now_iso


def append(
    conn: sqlite3.Connection,
    entity: str,
    entity_id: int | str | None,
    action: str,
    payload: dict | None = None,
    commit: bool = True,
) -> Event:
    """Append an event. commit=False lets callers batch many events in one
    transaction (the caller commits)."""
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO events(entity, entity_id, action, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            entity,
            str(entity_id) if entity_id is not None else None,
            action,
            json.dumps(payload or {}, ensure_ascii=False),
            ts,
        ),
    )
    if commit:
        conn.commit()
    return Event(
        id=cur.lastrowid,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        action=action,
        payload=payload or {},
        created_at=ts,
    )


def since(conn: sqlite3.Connection, last_id: int = 0, limit: int = 500) -> list[Event]:
    """All events with id > last_id, oldest first."""
    rows = conn.execute(
        "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
        (last_id, limit),
    ).fetchall()
    return [Event.from_row(r) for r in rows]


def last_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()
    return int(row["m"])


DEFAULT_PRUNE_KEEP = 10_000


def prune(conn: sqlite3.Connection, keep: int = DEFAULT_PRUNE_KEEP) -> int:
    """Keep only the last `keep` events (the bus is append-only; without this
    it grows forever). Returns the number of deleted rows. The GUI calls this
    once at startup; polling uses `since(last_id)` so old events are useless."""
    if keep < 0:
        keep = 0
    cur = conn.execute(
        "DELETE FROM events WHERE id <= (SELECT COALESCE(MAX(id), 0) - ? FROM events)",
        (keep,),
    )
    conn.commit()
    return cur.rowcount
