"""`devdash backup` — one-command, timestamped zip backup of DEVDASH_HOME.

The DB snapshot uses SQLite's online backup API, so it is consistent even
while the GUI is running. The zip contains devdash.db + config.json (the API
keys stay inside the backup — it is a LOCAL file, treat it accordingly).
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
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
EXPORT_TABLES = [
    "meta", "projects", "features", "tasks", "sessions",
    "log_entries", "git_links", "git_commits", "git_sync_state",
]


def backup_cmd(
    ctx: typer.Context,
    out: str | None = typer.Option(None, "--out", "-o", help=tr("opt.out_backup")),
) -> None:
    """Create a timestamped zip backup of the whole DevDash home (db + config)."""
    home = cfgmod.devdash_home()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(out).expanduser() if out else Path.cwd()
    outdir.mkdir(parents=True, exist_ok=True)
    zip_path = outdir / f"devdash-backup-{ts}.zip"

    # consistent snapshot of a possibly-WAL database (safe while the GUI runs)
    tmp = outdir / f".devdash-snapshot-{ts}.db"
    src = db.connect(cfgmod.db_path(home))
    try:
        dst = sqlite3.connect(str(tmp))
        src.backup(dst)
        dst.close()
    finally:
        src.close()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, "devdash.db")
        cfg_path = cfgmod.config_path(home)
        if cfg_path.exists():
            z.write(cfg_path, "config.json")
    tmp.unlink(missing_ok=True)

    data = {
        "path": str(zip_path),
        "bytes": zip_path.stat().st_size,
        "files": ["devdash.db", "config.json"],
    }
    emit(ctx, data, lambda: console.print(
        tr("backup.line", path=zip_path, kb=f"{data['bytes'] / 1024:.1f}")
    ))


def export_cmd(
    ctx: typer.Context,
    out: str | None = typer.Option(None, "--out", "-o", help=tr("opt.out_export")),
) -> None:
    """Export ALL DevDash data as one JSON file (archive / migration / inspection).

    Read-only: never touches the database. The zip `backup` is for restore;
    this JSON is for reading the data outside DevDash."""
    home = cfgmod.devdash_home()
    conn = db.connect(cfgmod.db_path(home))
    db.init_db(conn)
    payload = {
        "exported_at": now_iso(),
        "schema_version": db.SCHEMA_VERSION,
        "tables": {},
    }
    for table in EXPORT_TABLES:
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.Error:
            rows = []
        payload["tables"][table] = [dict(r) for r in rows]
    conn.close()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(out).expanduser() if out else Path.cwd() / f"devdash-export-{ts}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")
    n = sum(len(v) for v in payload["tables"].values())
    data = {"path": str(path), "rows": n, "tables": sorted(payload["tables"])}
    emit(ctx, data, lambda: console.print(
        tr("export.line", path=path, rows=n)
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
