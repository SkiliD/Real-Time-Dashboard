from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfgmod, db
from .models import now_iso

#: whitelist of exported tables (fixed names -> no SQL injection possible)
EXPORT_TABLES = [
    "meta", "projects", "features", "tasks", "sessions",
    "log_entries", "git_links", "git_commits", "git_sync_state", "ai_messages",
]


def _prune_backups(outdir: Path, keep: int) -> int:
    """Delete the oldest devdash-backup-*.zip beyond `keep` (by mtime).
    Returns the number of deleted files."""
    zips = sorted(outdir.glob("devdash-backup-*.zip"), key=lambda p: p.stat().st_mtime)
    old = zips[:-keep] if keep > 0 else zips
    deleted = 0
    for z in old:
        try:
            z.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


def maybe_auto_backup(home: Path | None = None, now: str | None = None) -> dict | None:
    """Automatic backup: if the last auto backup is older than `auto_backup_days`
    (0 = disabled), write a zip into <home>/backups and prune old ones (keep
    `auto_backup_keep`). Returns the backup payload, or None when skipped/failed.
    NEVER raises: a failed auto-backup must not block app startup."""
    home = home or cfgmod.devdash_home()
    cfg = cfgmod.load_config(home)
    if cfg.auto_backup_days <= 0:
        return None
    now = now or now_iso()
    if cfg.last_auto_backup:
        try:
            last = datetime.fromisoformat(cfg.last_auto_backup)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            cur = datetime.fromisoformat(now)
            if cur.tzinfo is None:
                cur = cur.replace(tzinfo=timezone.utc)
            if (cur - last).total_seconds() < cfg.auto_backup_days * 86400:
                return None  # recent enough
        except ValueError:
            pass  # unreadable stamp -> back up now
    try:
        outdir = home / "backups"
        r = make_backup(home=home, out_dir=outdir)
        r["pruned"] = _prune_backups(outdir, cfg.auto_backup_keep)
    except Exception:
        return None
    cfgmod.update_config(home, lambda c: setattr(c, "last_auto_backup", now))
    return r


def make_backup(home: Path | None = None, out_dir: str | Path | None = None) -> dict:
    """Create a timestamped zip backup of DEVDASH_HOME. Returns the payload
    {path, bytes, files} (same shape the CLI emits)."""
    home = home or cfgmod.devdash_home()
    outdir = Path(out_dir).expanduser() if out_dir else Path.cwd()
    outdir.mkdir(parents=True, exist_ok=True)
    # mkstemp = O_CREAT|O_EXCL: the name is unpredictable and the file cannot be
    # a pre-planted symlink (both snapshot and zip)
    fd, zip_name = tempfile.mkstemp(prefix="devdash-backup-", suffix=".zip", dir=outdir)
    os.close(fd)
    zip_path = Path(zip_name)

    # consistent snapshot of a possibly-WAL database (safe while the GUI runs)
    fd, tmp_name = tempfile.mkstemp(prefix=".devdash-snapshot-", suffix=".db", dir=outdir)
    os.close(fd)
    tmp = Path(tmp_name)
    src = db.connect(cfgmod.db_path(home))
    snapshot_ok = False
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
        finally:
            dst.close()
        snapshot_ok = True
    finally:
        src.close()
        if not snapshot_ok:
            # src.backup() raised before the zip step: never leave the temp
            # snapshot behind (on success the zip block below unlinks it)
            tmp.unlink(missing_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "devdash.db")
            cfg_path = cfgmod.config_path(home)
            if cfg_path.exists():
                z.write(cfg_path, "config.json")
    finally:
        tmp.unlink(missing_ok=True)
    # the zip contains config.json (API key): same user-only perms as the db/config
    from .db import restrict_permissions
    restrict_permissions(zip_path)

    return {
        "path": str(zip_path),
        "bytes": zip_path.stat().st_size,
        "files": ["devdash.db", "config.json"],
    }


def export_json(home: Path | None = None, out_dir: str | Path | None = None) -> dict:
    """Export ALL data as one JSON file (archive / migration / inspection).
    Read-only: never touches the database. Returns {path, rows, tables}."""

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

    # `out_dir` may be a target FILE (CLI --out path.json, historical behavior)
    # or a DIRECTORY (GUI passes <DEVDASH_HOME>/exports). Default: cwd.
    if out_dir:
        out = Path(out_dir).expanduser()
        if out.suffix == ".json":
            path = out
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            out.mkdir(parents=True, exist_ok=True)
            # mkstemp (O_EXCL) inside the directory: no predictable name, no symlink race
            fd, name = tempfile.mkstemp(prefix="devdash-export-", suffix=".json", dir=out)
            os.close(fd)
            path = Path(name)
    else:
        fd, name = tempfile.mkstemp(prefix="devdash-export-", suffix=".json", dir=Path.cwd())
        os.close(fd)
        path = Path(name)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")
    from .db import restrict_permissions
    restrict_permissions(path)  # the export contains the whole history: same perms as the db
    return {
        "path": str(path),
        "rows": sum(len(v) for v in payload["tables"].values()),
        "tables": sorted(payload["tables"]),
    }
