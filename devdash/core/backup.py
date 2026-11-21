"""One-command backup of the whole DevDash home (db + config) as a timestamped zip.

Used by the CLI (`devdash backup`) and the GUI (POST /api/backup).

The DB snapshot uses SQLite's online backup API, so it is consistent even
while the GUI is running. The zip contains devdash.db + config.json (the API
keys stay inside the backup — it is a LOCAL file, treat it accordingly).
"""

from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from . import config as cfgmod, db

#: whitelist of exported tables (fixed names -> no SQL injection possible)
EXPORT_TABLES = [
    "meta", "projects", "features", "tasks", "sessions",
    "log_entries", "git_links", "git_commits", "git_sync_state",
]


def make_backup(home: Path | None = None, out_dir: str | Path | None = None) -> dict:
    """Create a timestamped zip backup of DEVDASH_HOME. Returns the payload
    {path, bytes, files} (same shape the CLI emits)."""
    home = home or cfgmod.devdash_home()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(out_dir).expanduser() if out_dir else Path.cwd()
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
    # the zip contains config.json (API key): same 0600 as the db/config files
    # (best-effort — Windows only honors the read-only bit)
    try:
        zip_path.chmod(0o600)
    except OSError:
        pass

    return {
        "path": str(zip_path),
        "bytes": zip_path.stat().st_size,
        "files": ["devdash.db", "config.json"],
    }


def export_json(home: Path | None = None, out_dir: str | Path | None = None) -> dict:
    """Export ALL data as one JSON file (archive / migration / inspection).
    Read-only: never touches the database. Returns {path, rows, tables}."""
    import json
    import sqlite3

    from .models import now_iso

    home = home or cfgmod.devdash_home()
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
    # `out_dir` may be a target FILE (CLI --out path.json, historical behavior)
    # or a DIRECTORY (GUI passes <DEVDASH_HOME>/exports). Default: cwd.
    if out_dir:
        out = Path(out_dir).expanduser()
        path = out if out.suffix == ".json" else out / f"devdash-export-{ts}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = Path.cwd() / f"devdash-export-{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")
    return {
        "path": str(path),
        "rows": sum(len(v) for v in payload["tables"].values()),
        "tables": sorted(payload["tables"]),
    }
