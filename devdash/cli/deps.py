"""one sqlite connection per command + the guard() error boundary

If it breaks, the tests will say so.
"""

from __future__ import annotations

import sqlite3

import typer

from devdash.core import config, db
from devdash.core.i18n import tr
from devdash.core.models import MAX_TEXT_LEN


def read_text_file(path: str) -> str:
    """Read a whole text file (--desc-file / --content-file): the comfortable way
    to write long descriptions for long-term projects, from your editor."""
    from pathlib import Path

    p = Path(path).expanduser()
    try:
        data = p.read_text(encoding="utf-8")
    except OSError as e:
        raise typer.BadParameter(f"cannot read {path}: {e.strerror or e}") from e
    if len(data) > MAX_TEXT_LEN:
        raise typer.BadParameter(f"{path} is too large (max {MAX_TEXT_LEN} chars)")
    return data


def get_conn(ctx: typer.Context) -> sqlite3.Connection:
    """Open (and lazily create) the DevDash database for this command."""
    home = config.devdash_home()
    conn = db.connect(config.db_path(home))
    db.init_db(conn)
    ctx.call_on_close(conn.close)
    return conn


def resolve_project(conn: sqlite3.Connection, ref: str | None):
    """Resolve a --project option (slug or numeric id), else the active project.

    Raises a clean CLI error when there is no active project.
    """
    from devdash.core import projects as core_projects

    if ref is not None:
        try:
            ref_val: int | str = int(ref)
        except ValueError:
            ref_val = ref
        project = core_projects.get(conn, ref_val)
        if project is None:
            raise typer.BadParameter(tr("err.no_project", ref=ref))
        return project
    project = core_projects.get_active(conn)
    if project is None:
        raise typer.BadParameter(tr("err.no_active_project"))
    return project


def guard(fn, *args, **kwargs):
    """THE single CLI error boundary: run a core call and turn domain errors
    into a clean `Invalid value: ...` message instead of a raw traceback.
    KeyError = unknown id/ref, ValueError = invalid value, GitError = broken
    repo, IntegrityError = constraint (duplicate, bad FK), TypeError = caller
    passed a wrong type. Every command uses this — no per-command try/except."""
    from devdash.core.gitwatch import GitError

    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise typer.BadParameter(e.args[0] if e.args else "not found") from e
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    except GitError as e:
        raise typer.BadParameter(str(e)) from e
    except sqlite3.IntegrityError as e:
        raise typer.BadParameter(f"constraint violation: {e}") from e
    except TypeError as e:
        raise typer.BadParameter(str(e)) from e
