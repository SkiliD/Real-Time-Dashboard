# files endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from .api_common import _folder_picker_command
from devdash.core import config
from pathlib import Path
import os
import subprocess

class FilesMixin:
    def files_list(self) -> dict:
        """List backup zips and JSON exports inside DEVDASH_HOME (never outside)."""
        home = Path(self.home or config.devdash_home()).resolve()
        out = {"home": str(home), "backups": [], "exports": []}
        for sub, key in (("backups", "backups"), ("exports", "exports")):
            d = home / sub
            if d.is_dir():
                for f in sorted(d.iterdir(), key=lambda p: p.name, reverse=True)[:50]:
                    if f.is_file() and f.suffix in (".zip", ".json"):
                        st = f.stat()
                        out[key].append({"name": f.name, "bytes": st.st_size,
                                         "path": str(f)})
        return out

    # --- filesystem: native folder picker -----------------------------------


    def fs_pick_directory(self, body: dict) -> dict:
        """Open the OS folder picker on the desktop where the server runs.

        Returns {"path": str | null} (null = cancelled). The GUI only pre-fills
        the path input with the result — the user still confirms the link, so
        this endpoint never connects a project or a repo by itself.
        """
        title = self._str(body, "title") or "Select a folder"
        if len(title) > 120:
            raise ApiError("title is too long")
        cmd = _folder_picker_command(title)
        if cmd is None:
            raise ApiError("no folder picker available on this platform")
        env = dict(os.environ, DEVDASH_PICK_TITLE=title)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env,
                                  encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            raise ApiError("folder picker could not be started")
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        return {"path": lines[-1] if lines else None}

