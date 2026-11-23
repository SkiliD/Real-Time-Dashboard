# projects CRUD + the active-project pointer
# Read-only on purpose.

from __future__ import annotations

import builtins
import json
import re
import sqlite3
import unicodedata

from . import events
from .models import Project, now_iso

VALID_STATUSES = ("idea", "design", "prototype", "development", "beta", "done", "paused")
# engine is a free label; these are just the common ones suggested by the CLI/GUI
SUGGESTED_ENGINES = ("unity", "unreal", "godot", "web", "other")


def _slugify(name: str) -> str:
    # fold accents (é -> e) so "Jeu Été" becomes "jeu-ete", not "jeu-t"
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")
    return slug or "project"


def _unique_slug(conn: sqlite3.Connection, base: str) -> str:
    """Return base, or base-2, base-3, ... until the slug is free."""
    slug, i = base, 2
    while conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{i}"
        i += 1
    return slug


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

MAX_LANGUAGES = 12
MAX_LANG_LEN = 24


def _validate_color(color: str) -> str:
    """Colors end up in a CSS `style` attribute in the GUI: only allow #rrggbb,
    anything else would be an injection vector."""
    if not _HEX_COLOR.match(color):
        raise ValueError(f"invalid color {color!r}: expected #rrggbb")
    return color.lower()


def _validate_languages(languages) -> list[str]:
    """Normalize the per-project programming languages: free labels, lowercase,
    deduplicated, bounded (they are displayed in the UI and sent in the AI context)."""
    # NB: this module defines its own list() function, which shadows the builtin
    if not isinstance(languages, (builtins.list, builtins.tuple)):
        raise ValueError("languages must be a list of strings")
    out: list[str] = []
    for lang in languages:
        if not isinstance(lang, str):
            raise ValueError("languages must be a list of strings")
        lang = lang.strip().lower().replace(" ", "-")
        if not lang:
            continue
        if len(lang) > MAX_LANG_LEN:
            raise ValueError(f"language name too long: {lang!r} (max {MAX_LANG_LEN} chars)")
        if lang not in out:
            out.append(lang)
    if len(out) > MAX_LANGUAGES:
        raise ValueError(f"too many languages (max {MAX_LANGUAGES})")
    return out


def create(
    conn: sqlite3.Connection,
    name: str,
    description: str = "",
    engine: str = "other",
    color: str = "#8a8f98",
    status: str = "idea",
    languages: list[str] | None = None,
) -> Project:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("project name is required (non-empty)")
    name = name.strip()
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}, expected one of {VALID_STATUSES}")
    if not isinstance(engine, str) or not engine.strip() or len(engine) > 40:
        raise ValueError("engine must be a non-empty string of at most 40 characters")
    color = _validate_color(color)
    langs = _validate_languages(languages or [])
    now = now_iso()
    slug = _unique_slug(conn, _slugify(name))
    for _attempt in range(5):  # bounded retry on a slug race
        try:
            with conn:  # row + event in one transaction
                cur = conn.execute(
                    "INSERT INTO projects(slug, name, description, engine, color, languages, "
                    "status, archived, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (slug, name, description, engine.lower(), color,
                     json.dumps(langs), status, now, now),
                )
                project = get(conn, cur.lastrowid)
                assert project is not None
                events.append(conn, "project", project.id, "created", project.to_dict(), commit=False)
            break
        except sqlite3.IntegrityError:
            # concurrent create grabbed the same slug between check and insert
            conn.rollback()
            slug = _unique_slug(conn, _slugify(name))
    else:
        raise ValueError(f"could not allocate a unique slug for {name!r}")
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
    with conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('active_project_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(project.id),),
        )
        events.append(conn, "project", project.id, "activated", {"slug": project.slug}, commit=False)
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
    languages: list[str] | None = None,
) -> Project:
    project = get(conn, ref)
    if project is None:
        raise KeyError(f"no project matching {ref!r}")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}, expected one of {VALID_STATUSES}")
    if color is not None:
        color = _validate_color(color)
    if engine is not None and (not isinstance(engine, str) or not engine.strip() or len(engine) > 40):
        raise ValueError("engine must be a non-empty string of at most 40 characters")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ValueError("project name cannot be empty")
    fields, values = [], []
    for column, value in (
        ("name", name.strip() if isinstance(name, str) else name),
        ("description", description),
        ("engine", engine.lower() if engine else None),
        ("color", color),
        ("status", status),
        ("archived", None if archived is None else int(archived)),
        # None = "no change"; an explicit list (even empty) replaces the languages
        ("languages", None if languages is None else json.dumps(_validate_languages(languages))),
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
    return project  # no field changed: no write, no event


def delete(conn: sqlite3.Connection, ref: int | str) -> Project:
    """Hard-delete a project and everything attached to it.

    The schema cascades: tasks (and their subtasks), features, sessions,
    devlog entries and the git LINK go with the project row. Imported git
    commits and sync state are deliberately KEPT (no FK by design): they are
    the repo's history, keyed by repo_path, and may still be useful.
    If the deleted project was the active one, the active pointer moves to the
    first remaining project (or none when no project is left).
    Explicit human action only — never called automatically.
    """
    project = get(conn, ref)
    if project is None:
        raise KeyError(f"no project matching {ref!r}")
    with conn:  # one transaction: bus cleanup + row delete + active pointer
        conn.execute(
            "DELETE FROM events WHERE entity = 'project' AND entity_id = ?",
            (project.id,),
        )
        conn.execute("DELETE FROM projects WHERE id = ?", (project.id,))
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'active_project_id'"
        ).fetchone()
        if row and int(row["value"]) == project.id:
            conn.execute("DELETE FROM meta WHERE key = 'active_project_id'")
    events.append(conn, "project", project.id, "deleted",
                  {"slug": project.slug, "name": project.name})
    if get_active_id(conn) is None:
        remaining = list(conn, include_archived=True)
        if remaining:
            set_active(conn, remaining[0].id)
    return project
