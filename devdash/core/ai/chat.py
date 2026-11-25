"""Persisted AI chat history (one row per message, per project).

Written only by the ask endpoint (user question + assistant answer) and read by
the GUI chat tab — so the conversation survives a reload/restart. Old rows are
pruned per project (keep the newest MAX_HISTORY).
"""

from __future__ import annotations

import sqlite3

from .. import i18n
from ..models import now_iso

MAX_HISTORY = 500   # messages kept per project (oldest pruned on append)
HISTORY_LIMIT = 200  # messages sent to the GUI in one page


def append(conn: sqlite3.Connection, project_id: int, role: str, content: str,
           meta: str | None = None) -> None:
    if role not in ("user", "assistant"):
        raise ValueError(i18n.tr("err.chat_role", role=role))
    with conn:
        conn.execute(
            "INSERT INTO ai_messages(project_id, role, content, meta, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, role, content, meta, now_iso()),
        )
        conn.execute(
            "DELETE FROM ai_messages WHERE project_id = ? AND id NOT IN "
            "(SELECT id FROM ai_messages WHERE project_id = ? ORDER BY id DESC LIMIT ?)",
            (project_id, project_id, MAX_HISTORY),
        )


def history(conn: sqlite3.Connection, project_id: int, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Messages oldest-first (chronological), capped at `limit`."""
    rows = conn.execute(
        "SELECT id, role, content, meta, created_at FROM ai_messages "
        "WHERE project_id = ? ORDER BY id DESC LIMIT ?",
        (project_id, max(1, min(limit, MAX_HISTORY))),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear(conn: sqlite3.Connection, project_id: int) -> int:
    with conn:
        cur = conn.execute("DELETE FROM ai_messages WHERE project_id = ?", (project_id,))
        return cur.rowcount
