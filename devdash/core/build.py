# build the standalone devdash executable with PyInstaller — local, no network
# Works on Windows, Mac and Linux.

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
from pathlib import Path

BUILD_TIMEOUT = 1800  # PyInstaller on a cold cache can take several minutes
LOG_TAIL = 4000       # chars of build log kept for the GUI


def package_root() -> Path:
    """Repo root when devdash is imported from a source checkout."""
    return Path(__file__).resolve().parent.parent.parent


def pyinstaller_available() -> bool:
    return importlib.util.find_spec("PyInstaller") is not None


def exe_path(root: Path | None = None, onefile: bool = False) -> Path:
    root = root or package_root()
    name = "devdash.exe" if sys.platform == "win32" else "devdash"
    return root / "dist" / name if onefile else root / "dist" / "devdash" / name


def build_exe(
    onefile: bool = False,
    no_gui: bool = False,
    no_console: bool = False,
    root: Path | None = None,
    timeout: int = BUILD_TIMEOUT,
) -> dict:
    """Run PyInstaller on devdash.spec (synchronous). Never raises for build
    failures: returns {"ok", "exe", "log_tail", "error"}."""
    root = root or package_root()
    spec = root / "devdash.spec"
    if not spec.is_file():
        return {"ok": False, "exe": None, "log_tail": "", "error": f"spec not found: {spec}"}
    if not pyinstaller_available():
        return {
            "ok": False, "exe": None, "log_tail": "",
            "error": 'PyInstaller is not installed: run pip install -e ".[build]" first',
        }
    env = dict(os.environ)
    if onefile:
        env["DEVDASH_ONEFILE"] = "1"
    if no_gui:
        env["DEVDASH_SPEC_NO_GUI"] = "1"
    if no_console:
        env["DEVDASH_CONSOLE"] = "0"
    cmd = [
        sys.executable, "-m", "PyInstaller", str(spec), "--noconfirm", "--clean",
        f"--distpath={root / 'dist'}", f"--workpath={root / 'build'}",
    ]
    try:
        r = subprocess.run(
            cmd, cwd=root, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "exe": None, "log_tail": "", "error": str(e)}
    exe = exe_path(root, onefile)
    ok = r.returncode == 0 and exe.is_file()
    return {
        "ok": ok,
        "exe": str(exe) if exe.is_file() else None,
        "log_tail": out[-LOG_TAIL:],
        "error": None if r.returncode == 0 else f"PyInstaller exited with code {r.returncode}",
    }


# --- background runner for the GUI (one build at a time) ---------------------

_lock = threading.Lock()
_state: dict = {"running": False, "ok": None, "exe": None, "error": None, "log_tail": ""}


def build_status() -> dict:
    with _lock:
        return dict(_state)


def start_build(onefile: bool = False, no_gui: bool = False, no_console: bool = False) -> dict:
    """Start a background PyInstaller build (used by the GUI). Returns
    {"started": True} or {"started": False, "error": ...} when a build is
    already running or PyInstaller is missing. Poll build_status()."""
    with _lock:
        if _state["running"]:
            return {"started": False, "error": "a build is already running"}
        if not pyinstaller_available():
            return {
                "started": False,
                "error": 'PyInstaller is not installed: run pip install -e ".[build]" first',
            }
        _state.update(running=True, ok=None, exe=None, error=None, log_tail="")

    def _run() -> None:
        try:
            result = build_exe(onefile=onefile, no_gui=no_gui, no_console=no_console)
        except Exception as e:  # never leave running=True stuck: all later builds refused
            result = {"ok": False, "exe": None, "log_tail": "", "error": f"unexpected build error: {e}"}
        with _lock:
            _state.update(
                running=False, ok=result["ok"], exe=result["exe"],
                error=result["error"], log_tail=result["log_tail"],
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True}
