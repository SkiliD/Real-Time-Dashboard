# system endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from devdash.core import config
from devdash.core import projects as proj
from pathlib import Path
import os
import subprocess
import sys

class SystemMixin:
    def backup_now(self, body: dict) -> dict:
        """One-click backup from the GUI: timestamped zip of the whole home.
        Default destination is <DEVDASH_HOME>/backups (never the cwd)."""
        from devdash.core.backup import make_backup
        home = self.home or config.devdash_home()
        try:
            return make_backup(home=home, out_dir=self._confined_out(body, "backups"))
        except OSError as e:
            raise ApiError(f"backup failed: {e}")


    def export_now(self, body: dict) -> dict:
        """One-click JSON export from the GUI (read-only)."""
        from devdash.core.backup import export_json
        home = self.home or config.devdash_home()
        try:
            return export_json(home=home, out_dir=self._confined_out(body, "exports"))
        except OSError as e:
            raise ApiError(f"export failed: {e}")

    # --- self-update ---------------------------------------------------------


    def update_status(self) -> dict:
        """Version + install mode + local git info + cheap LOCAL update detection
        (rev-list against the last FETCHED upstream — no network). Read-only."""
        from devdash import __version__
        from devdash.core import updater
        r = {"version": __version__, **updater.git_status()}
        chk = updater.check_for_updates(fetch=False)  # local only: uses the last fetch
        if chk.get("git") and "update_available" in chk:
            r["update_available"] = chk["update_available"]
            r["behind"] = chk.get("behind", 0)
        return r


    def update_check(self, body: dict | None = None) -> dict:
        """git fetch + compare with upstream (network, read-only on the tree)."""
        from devdash.core import updater
        return updater.check_for_updates()


    def update_now(self, body: dict) -> dict:
        """Apply the update: git pull --ff-only + pip install -e . (git mode).
        Aborts on a dirty tree. Mutating route: Host/Origin guards apply, AND
        it requires an explicit confirm=true (the GUI arms the button first,
        same two-step pattern as delete/demo)."""
        from devdash.core import updater
        if not (body or {}).get("confirm"):
            raise ApiError("update requires confirm=true (two-step in the GUI)", status=400)
        return updater.update(reinstall=bool((body or {}).get("reinstall", True)))


    # --- build the standalone exe --------------------------------------------

    def build_status(self) -> dict:
        """Background build state (running/ok/exe/log tail). Read-only."""
        from devdash.core import build as build_core
        return build_core.build_status()

    def build_run(self, body: dict) -> dict:
        """Start a background PyInstaller build of devdash.exe (local, no
        network). Mutating route: Host/Origin guards apply AND it requires an
        explicit confirm=true (the GUI arms the button first, same two-step
        pattern as /api/update and delete). Refuses while another build runs.
        Poll /api/build/status for the result."""
        from devdash.core import build as build_core
        if not (body or {}).get("confirm"):
            raise ApiError("build requires confirm=true (two-step in the GUI)", status=400)
        return build_core.start_build(
            onefile=bool((body or {}).get("onefile")),
            no_gui=bool((body or {}).get("no_gui")),
            no_console=bool((body or {}).get("no_console")),
        )

    def open_home(self, body: dict | None = None) -> dict:
        """Reveal DEVDASH_HOME in the OS file explorer. The folder is only opened
        when the client explicitly sends open=true (GUI button); otherwise the
        endpoint just returns the path."""
        import os
        import subprocess
        import sys

        home = Path(self.home or config.devdash_home()).resolve()
        if (body or {}).get("open"):
            try:
                if sys.platform == "win32" and hasattr(os, "startfile"):
                    os.startfile(home)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(home)])
                else:
                    subprocess.Popen(["xdg-open", str(home)])
            except OSError as e:
                raise ApiError(f"cannot open folder: {e}")
        return {"path": str(home)}


    def demo_seed(self, body: dict) -> dict:
        """Seed demo data from the GUI. Refuses when projects already exist
        unless force=true (same rule as `devdash demo seed --force`)."""
        from devdash.core import demo as demo_core
        with self._conn() as c:
            if proj.list(c, include_archived=True) and not body.get("force"):
                raise ApiError("projects already exist — pass force=true to add demo data anyway")
            return demo_core.seed(c)

