"""Shared CLI dependencies: one SQLite connection per command invocation.

The DB lives in DEVDASH_HOME (default ~/.devdash) and is auto-initialized on
first use, so there is no separate `init` step to remember.
"""

from __future__ import annotations

import sqlite3

import typer

from devdash.core import config, db
from devdash.core.i18n import tr


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
    (KeyError = unknown id/ref, ValueError = invalid value) into a clean
    `Invalid value: ...` message instead of a raw traceback. Every command
    uses this — no per-command try/except anywhere else."""
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise typer.BadParameter(e.args[0] if e.args else "not found") from e
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
