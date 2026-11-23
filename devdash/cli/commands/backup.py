"""devdash backup -- zip of the whole devdash home

Small on purpose -- it has to stay fast.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from devdash.core import config as cfgmod, db, events as events_mod
from devdash.core.i18n import tr
from devdash.core.models import now_iso
from ..output import emit

console = Console()

# whitelist of exported tables (fixed names -> no SQL injection possible)
from devdash.core.backup import EXPORT_TABLES  # noqa: E402  (shared with core)


def backup_cmd(
    ctx: typer.Context,
    out: str | None = typer.Option(None, "--out", "-o", help=tr("opt.out_backup")),
) -> None:
    """Create a timestamped zip backup of the whole DevDash home (db + config)."""
    from devdash.core.backup import make_backup

    data = make_backup(out_dir=out)
    emit(ctx, data, lambda: console.print(
        tr("backup.line", path=data["path"], kb=f"{data['bytes'] / 1024:.1f}")
    ))


def export_cmd(
    ctx: typer.Context,
    out: str | None = typer.Option(None, "--out", "-o", help=tr("opt.out_export")),
) -> None:
    """Export ALL DevDash data as one JSON file (archive / migration / inspection).

    Read-only: never touches the database. The zip `backup` is for restore;
    this JSON is for reading the data outside DevDash."""
    from devdash.core.backup import export_json

    data = export_json(out_dir=out)
    emit(ctx, data, lambda: console.print(
        tr("export.line", path=data["path"], rows=data["rows"])
    ))


def import_cmd(
    ctx: typer.Context,
    file: str = typer.Argument(..., help=tr("opt.import_file")),
    force: bool = typer.Option(False, "--force", "-f", help=tr("opt.force_import")),
) -> None:
    """Restore data from a `devdash export` JSON file.

    SAFETY: refuses to merge into a non-empty database unless --force is given
    (--force REPLACES all imported tables). The zip `backup` remains the way to
    restore config.json (API keys); this command only restores the DB data."""
    import typer as _typer

    path = Path(file).expanduser()
    if not path.is_file():
        raise _typer.BadParameter(tr("err.import_no_file", file=file))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise _typer.BadParameter(tr("err.import_bad_json", err=e)) from e
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise _typer.BadParameter(tr("err.import_bad_json", err="missing 'tables'"))

    home = cfgmod.devdash_home()
    conn = db.connect(cfgmod.db_path(home))
    db.init_db(conn)
    try:
        existing = conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]
        if existing and not force:
            raise _typer.BadParameter(tr("err.import_not_empty"))
        # interactively ask once; in --json / piped mode --force alone is the consent
        interactive = not (ctx.obj and ctx.obj.get("json")) and sys.stdin.isatty()
        if existing and force and interactive and not _typer.confirm(tr("import.force_confirm"), default=False):
            raise _typer.Abort()
        # column names come from the file: validate them against the real schema
        # (whitelist of tables, whitelist of columns -> no SQL injection)
        conn.execute("PRAGMA foreign_keys = OFF")
        total = 0
        errors = 0
        for table in EXPORT_TABLES:
            rows = tables.get(table) or []
            valid_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if table == "meta":  # never overwrite the schema stamp via import
                rows = [r for r in rows if r.get("key") != "schema_version"]
            conn.execute(f"DELETE FROM {table}")
            for row in rows:
                cols = [c for c in row.keys() if c in valid_cols]  # hostile cols ignored
                if not cols:
                    errors += 1
                    continue
                try:
                    conn.execute(
                        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols],
                    )
                    total += 1
                except sqlite3.Error:
                    errors += 1  # a restore tool must skip bad rows, not crash
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        events_mod.append(conn, "import", None, "imported", {"file": str(path), "rows": total, "errors": errors})
    finally:
        conn.close()

    data = {"file": str(path), "rows": total, "errors": errors, "force": force}
    emit(ctx, data, lambda: console.print(tr("import.line", file=path, rows=total)))
