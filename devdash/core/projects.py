# projects CRUD + the active-project pointer
# Read-only on purpose.

from __future__ import annotations

import builtins
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

from . import events, i18n
from .models import Project, check_text, now_iso

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
_ENGINE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _validate_engine(engine) -> str:
    """Engine is a free label, but it is rendered in the GUI (sidebar/header) and
    sent to the AI context: restrict it to a safe slug charset, lowercased —
    anything else (HTML, spaces, punctuation) is refused."""
    if not isinstance(engine, str):
        raise ValueError(i18n.tr("err.engine_type"))
    e = engine.strip().lower()
    if not _ENGINE_RE.match(e):
        raise ValueError(i18n.tr("err.engine_format"))
    return e

MAX_LANGUAGES = 12
MAX_LANG_LEN = 24
MAX_PATH_LEN = 4096


def _validate_path(path) -> str:
    """Connected project folder: empty = none; otherwise it must be an existing
    directory and is stored resolved (absolute). The path is only ever used to
    open the OS file explorer — never followed anywhere else."""
    if path is None:
        return ""
    if not isinstance(path, str):
        raise ValueError(i18n.tr("err.path_type"))
    raw = path.strip()
    if not raw:
        return ""
    if len(raw) > MAX_PATH_LEN:
        raise ValueError(i18n.tr("err.path_too_long", max=MAX_PATH_LEN))
    p = Path(raw).expanduser()
    if not p.is_dir():
        raise ValueError(i18n.tr("err.not_a_dir", path=raw))
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def _validate_color(color: str) -> str:
    """Colors end up in a CSS `style` attribute in the GUI: only allow #rrggbb,
    anything else would be an injection vector."""
    if not _HEX_COLOR.match(color):
        raise ValueError(i18n.tr("err.color_invalid", color=color))
    return color.lower()


def _validate_languages(languages) -> list[str]:
    """Normalize the per-project programming languages: free labels, lowercase,
    deduplicated, bounded (they are displayed in the UI and sent in the AI context)."""
    # NB: this module defines its own list() function, which shadows the builtin
    if not isinstance(languages, (builtins.list, builtins.tuple)):
        raise ValueError(i18n.tr("err.languages_type"))
    out: list[str] = []
    for lang in languages:
        if not isinstance(lang, str):
            raise ValueError(i18n.tr("err.languages_type"))
        lang = lang.strip().lower().replace(" ", "-")
        if not lang:
            continue
        if len(lang) > MAX_LANG_LEN:
            raise ValueError(i18n.tr("err.lang_too_long", lang=lang, max=MAX_LANG_LEN))
        if lang not in out:
            out.append(lang)
    if len(out) > MAX_LANGUAGES:
        raise ValueError(i18n.tr("err.too_many_langs", max=MAX_LANGUAGES))
    return out


def create(
    conn: sqlite3.Connection,
    name: str,
    description: str = "",
    engine: str = "other",
    color: str = "#8a8f98",
    status: str = "idea",
    languages: list[str] | None = None,
    path: str = "",
) -> Project:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(i18n.tr("err.project_name_required"))
    name = name.strip()
    description = check_text(description or "", "description")
    if status not in VALID_STATUSES:
        raise ValueError(i18n.tr("err.status_invalid", status=status, items=VALID_STATUSES))
    engine = _validate_engine(engine)
    color = _validate_color(color)
    langs = _validate_languages(languages or [])
    folder = _validate_path(path)
    now = now_iso()
    slug = _unique_slug(conn, _slugify(name))
    for _attempt in range(5):  # bounded retry on a slug race
        try:
            with conn:  # row + event in one transaction
                cur = conn.execute(
                    "INSERT INTO projects(slug, name, description, engine, color, languages, "
                    "path, status, archived, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (slug, name, description, engine, color,
                     json.dumps(langs), folder, status, now, now),
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
        raise ValueError(i18n.tr("err.slug_unavailable", name=name))
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
        raise KeyError(i18n.tr("err.no_project", ref=ref))
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
    if not row:
        return None
    try:  # a hand-edited/corrupted meta value must not crash the GUI with a 500
        return int(row["value"])
    except (TypeError, ValueError):
        return None


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
    path: str | None = None,
) -> Project:
    project = get(conn, ref)
    if project is None:
        raise KeyError(i18n.tr("err.no_project", ref=ref))
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(i18n.tr("err.status_invalid", status=status, items=VALID_STATUSES))
    if color is not None:
        color = _validate_color(color)
    if description is not None:
        description = check_text(description, "description")
    if engine is not None:
        engine = _validate_engine(engine)
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ValueError(i18n.tr("err.project_name_required"))
    fields, values = [], []
    for column, value in (
        ("name", name.strip() if isinstance(name, str) else name),
        ("description", description),
        ("engine", engine),
        ("color", color),
        ("status", status),
        ("archived", None if archived is None else int(archived)),
        # None = "no change"; an explicit list (even empty) replaces the languages
        ("languages", None if languages is None else json.dumps(_validate_languages(languages))),
        # None = "no change"; an explicit string (even empty) sets/clears the folder
        ("path", None if path is None else _validate_path(path)),
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
        raise KeyError(i18n.tr("err.no_project", ref=ref))
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
