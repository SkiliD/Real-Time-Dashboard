"""Open a folder in the OS file explorer — explicit user action only.

Confinement: callers only ever pass a path stored server-side (the project
folder set at `projects connect` / update, or the linked git repo). The
process is launched as an argv list, never through a shell, and detached.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .i18n import tr


def open_in_explorer(path: str) -> None:
    """Open `path` with the platform file explorer. Raises OSError on failure.
    Defense in depth: only directories are opened (on Windows `explorer.exe
    <file>` would EXECUTE an executable)."""
    if not Path(path).is_dir():
        raise OSError(tr("err.not_a_dir", path=path))
    if sys.platform.startswith("win"):
        cmd = ["explorer.exe", path]
    elif sys.platform == "darwin":
        cmd = ["open", path]
    else:
        cmd = ["xdg-open", path]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
