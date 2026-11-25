# Shared pieces of the GUI REST layer: error type + module-level helpers.
from __future__ import annotations

import shutil
import sys

class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# List endpoints are polled every 1.5 s by the GUI: long text fields are truncated
# in list payloads (full text stays available via the /show endpoints).
LIST_DESC_MAX = 300   # task description chars kept in /api/tasks list rows
LIST_CONTENT_MAX = 500  # log content chars kept in /api/logs list rows


def truncate_field(d: dict, field: str, max_len: int) -> dict:
    """Truncate a long text field in a list payload, in place, and flag it."""
    v = d.get(field)
    if isinstance(v, str) and len(v) > max_len:
        d[field] = v[:max_len] + "…"
        d[field + "_truncated"] = True
    return d


def _folder_picker_command(title: str, platform: str | None = None) -> list[str] | None:
    """OS-native folder-picker command (stdlib subprocess only — the HTTP server
    thread must not touch tkinter). Prints the chosen path on stdout, nothing
    when cancelled. `platform` is injectable for tests."""
    plat = platform or sys.platform
    if plat == "win32":
        # -Sta: FolderBrowserDialog needs an STA thread; title via env var to
        # avoid any quoting issue in the PowerShell snippet
        return ["powershell", "-NoProfile", "-Sta", "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$d.Description = $env:DEVDASH_PICK_TITLE; "
                "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $d.SelectedPath }"]
    if plat == "darwin":
        return ["osascript", "-e",
                "on run argv\n"
                'tell application "System Events" to activate\n'
                "set p to choose folder with prompt (item 1 of argv)\n"
                "POSIX path of p\n"
                "end run",
                "--", title]
    if shutil.which("zenity"):
        return ["zenity", "--file-selection", "--directory", f"--title={title}"]
    if shutil.which("kdialog"):
        return ["kdialog", "--getexistingdirectory", ".", f"--title={title}"]
    return None


def _general_block(cfg, home) -> dict:
    """The non-AI part of the settings payload (shared by get and save)."""
    from devdash.core import i18n
    return {
        "lang": i18n.get_lang(home),
        "langs": list(i18n.SUPPORTED_LANGS),
        "pomodoro_focus_min": cfg.pomodoro_focus_min,
        "pomodoro_break_min": cfg.pomodoro_break_min,
        "git_watch_interval_sec": cfg.git_watch_interval_sec,
        "gui_port": cfg.gui_port,
        "theme": cfg.theme,
        "auto_backup_days": cfg.auto_backup_days,
        "auto_backup_keep": cfg.auto_backup_keep,
        "last_auto_backup": cfg.last_auto_backup,
        "commit_patterns": list(cfg.commit_patterns),
    }


