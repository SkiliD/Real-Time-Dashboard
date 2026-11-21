"""Projects CRUD + the single "active project" pointer.

Switching the active project only updates meta.active_project_id: NO data of
any project is ever touched, so switching focus is instant and lossless.
Projects are soft-archived (archived=1), never hard-deleted.
"""

from __future__ import annotations

import re
import sqlite3

from . import events
from .models import Project, now_iso

VALID_STATUSES = ("idea", "design", "prototype", "development", "beta", "done", "paused")
# engine is a free label; these are just the common ones suggested by the CLI/GUI
SUGGESTED_ENGINES = ("unity", "unreal", "godot", "web", "other")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def _unique_slug(conn: sqlite3.Connection, base: str) -> str:
    """Return base, or base-2, base-3, ... until the slug is free."""
    slug, i = base, 2
    while conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{i}"
        i += 1
    return slug


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_color(color: str) -> str:
    """Colors end up in a CSS `style` attribute in the GUI: only allow #rrggbb,
    anything else would be an injection vector."""
    if not _HEX_COLOR.match(color):
        raise ValueError(f"invalid color {color!r}: expected #rrggbb")
    return color.lower()


def create(
    conn: sqlite3.Connection,
    name: str,
    description: str = "",
    engine: str = "other",
    color: str = "#8a8f98",
    status: str = "idea",
) -> Project:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}, expected one of {VALID_STATUSES}")
    color = _validate_color(color)
    now = now_iso()
    slug = _unique_slug(conn, _slugify(name))
    try:
        cur = conn.execute(
            "INSERT INTO projects(slug, name, description, engine, color, status, "
            "archived, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (slug, name, description, engine.lower(), color, status, now, now),
        )
    except sqlite3.IntegrityError:
        # concurrent create grabbed the same slug between check and insert
        conn.rollback()
        slug = _unique_slug(conn, _slugify(name))
        cur = conn.execute(
            "INSERT INTO projects(slug, name, description, engine, color, status, "
            "archived, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (slug, name, description, engine.lower(), color, status, now, now),
        )
    conn.commit()
    project = get(conn, cur.lastrowid)
    assert project is not None
    events.append(conn, "project", project.id, "created", project.to_dict())
    return project


def list(conn: sqlite3.Connection, include_archived: bool = False) -> list[Project]:
    if include_archived:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY archived ASC, created_at ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM projects WHERE archived = 0 ORDER BY created_at ASC"
        ).fetchall()
    return [Project.from_row(r) for r in rows]


def get(conn: sqlite3.Connection, ref: int | str) -> Project | None:
    """Look up a project by numeric id or by slug."""
    if isinstance(ref, int):
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (ref,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM projects WHERE slug = ?", (ref,)).fetchone()
    return Project.from_row(row) if row else None


def set_active(conn: sqlite3.Connection, ref: int | str) -> Project:
    """Point the focus at another project. Nothing else is modified."""
    project = get(conn, ref)
    if project is None:
        raise KeyError(f"no project matching {ref!r}")
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('active_project_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(project.id),),
    )
    conn.commit()
    events.append(conn, "project", project.id, "activated", {"slug": project.slug})
    return project


def get_active_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'active_project_id'"
    ).fetchone()
    return int(row["value"]) if row else None


def get_active(conn: sqlite3.Connection) -> Project | None:
    pid = get_active_id(conn)
    return get(conn, pid) if pid is not None else None


def update(
    conn: sqlite3.Connection,
    ref: int | str,
    *,
    name: str | None = None,
    description: str | None = None,
    engine: str | None = None,
    color: str | None = None,
    status: str | None = None,
    archived: bool | None = None,
) -> Project:
    project = get(conn, ref)
    if project is None:
        raise KeyError(f"no project matching {ref!r}")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}, expected one of {VALID_STATUSES}")
    if color is not None:
        color = _validate_color(color)
    fields, values = [], []
    for column, value in (
        ("name", name),
        ("description", description),
        ("engine", engine.lower() if engine else None),
        ("color", color),
        ("status", status),
        ("archived", None if archived is None else int(archived)),
    ):
        if value is not None:
            fields.append(f"{column} = ?")
            values.append(value)
    # note: renaming a project keeps its slug stable (slug is the stable id)
    if fields:
        fields.append("updated_at = ?")
        values.append(now_iso())
        values.append(project.id)
        conn.execute(
            f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", values
        )
        conn.commit()
    project = get(conn, project.id)
    assert project is not None
    events.append(conn, "project", project.id, "updated", project.to_dict())
    return project
