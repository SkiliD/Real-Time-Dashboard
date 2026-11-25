# Shared pieces of the GUI REST layer: error type + module-level helpers.
from __future__ import annotations

import shutil
import sys

class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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


