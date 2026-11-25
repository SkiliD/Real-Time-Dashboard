"""Timeline: the append-only event bus rendered as an activity feed.

Every core mutation appends an `events` row (task created/done, session
started/stopped, log entry, git commit, ...). This module turns that bus into
a queryable, project-scoped feed for the GUI tab, the CLI (`devdash timeline`)
and MCP (`devdash_timeline`) — pure read, no new writes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .i18n import tr
from .models import Event

MAX_LIMIT = 500
EVENT_LABEL_SNIPPET = 140  # one-line event detail chars kept in the timeline feed
# fetch window multiplier: project filtering happens in Python (the payload
# carries project_id, but not in a SQL-indexable column), so read more rows
# than the requested limit and trim afterwards
_WINDOW = 4
_MAX_WINDOW = 2000

ENTITIES = ("task", "session", "log", "git", "feature", "project", "config")

# entity -> icon used by the GUI feed (and the CLI table)
ICONS = {
    "task": "📝", "session": "⏱", "log": "📓", "git": "🌿",
    "feature": "⚑", "project": "📁", "config": "⚙",
}


def event_project(conn: sqlite3.Connection, e: Event,
                  repo_map: dict[str, int] | None = None) -> int | None:
    """Best-effort project id of an event, for project scoping.

    Most payloads carry project_id; project/git-link events use entity_id;
    git commit events are resolved through the linked repo path. Pass repo_map
    (repo_path -> project_id, raw + resolved) to avoid one query PER EVENT —
    list() builds it once per call. Returns None when the event is global
    (config) or cannot be attributed.
    """
    payload = e.payload if isinstance(e.payload, dict) else {}
    pid = payload.get("project_id")
    if isinstance(pid, int) and not isinstance(pid, bool):
        return pid
    if e.entity == "project" and e.entity_id is not None:
        try:
            return int(e.entity_id)
        except (TypeError, ValueError):
            return None
    if e.entity == "git":
        if e.action in ("linked", "unlinked") and e.entity_id is not None:
            try:
                return int(e.entity_id)
            except (TypeError, ValueError):
                return None
        repo = payload.get("repo_path")
        if repo:
            for cand in (str(repo), str(Path(repo).resolve())):
                if repo_map is not None:
                    if cand in repo_map:
                        return repo_map[cand]
                    continue
                row = conn.execute(
                    "SELECT project_id FROM git_links WHERE repo_path = ?", (cand,)
                ).fetchone()
                if row:
                    return int(row["project_id"])
    return None


def list(
    conn: sqlite3.Connection,
    project_id: int | None = None,
    entity: str | None = None,
    limit: int = 150,
) -> list[dict]:
    """Recent events, NEWEST FIRST. project_id scopes to one project (events
    that cannot be attributed to a project are then excluded); None = all."""
    if entity is not None and entity not in ENTITIES:
        raise ValueError(tr("err.entity_invalid", entity=entity, items=ENTITIES))
    limit = min(max(1, int(limit)), MAX_LIMIT)
    # repo_path -> project_id, loaded ONCE (raw + resolved forms): git commit events
    # are attributed through it without one SQL query per event
    repo_map: dict[str, int] | None = None
    if project_id is not None:
        repo_map = {}
        for row in conn.execute("SELECT repo_path, project_id FROM git_links").fetchall():
            pid = int(row["project_id"])
            repo_map[str(row["repo_path"])] = pid
            try:
                repo_map[str(Path(row["repo_path"]).resolve())] = pid
            except OSError:
                pass
    clauses, values = [], []
    if entity is not None:
        clauses.append("entity = ?")
        values.append(entity)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    fetch = min(limit * _WINDOW, _MAX_WINDOW) if project_id is not None else limit
    rows = conn.execute(
        f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?",
        (*values, fetch),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        e = Event.from_row(r)
        if project_id is not None:
            ep = event_project(conn, e, repo_map)
            if ep != project_id:
                continue
        d = e.to_dict()
        d["icon"] = ICONS.get(e.entity, "•")
        out.append(d)
        if len(out) >= limit:
            break
    return out


def detail(e: dict) -> str:
    """One-line human detail extracted from the payload (title, message...)."""
    pl = e.get("payload") or {}
    for key in ("title", "message", "content", "slug", "name", "repo_path", "note"):
        v = pl.get(key)
        if isinstance(v, str) and v.strip():
            text = v.strip().replace("\n", " ")
            return text[:EVENT_LABEL_SNIPPET]
    if e.get("entity") == "git" and e.get("action") == "commit":
        return str(pl.get("short") or e.get("entity_id") or "")[:EVENT_LABEL_SNIPPET]
    return ""


def label(e: dict) -> str:
    """Translated label for an event: tl.<entity>.<action>, with the stored
    status values mapped through tstatus.* and a generic fallback."""
    entity = e.get("entity") or "event"
    action = e.get("action") or ""
    if action.startswith("status:"):
        return tr("tstatus." + action.split(":", 1)[1])
    key = f"tl.{entity}.{action}"
    from .i18n import MESSAGES, get_lang
    table = MESSAGES.get(get_lang()) or MESSAGES["fr"]
    if key in table:
        return tr(key)
    base = f"tl.{entity}"
    return tr(base) if base in table else entity
