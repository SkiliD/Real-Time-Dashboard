"""Build a standalone DevDash executable with PyInstaller (no Python needed at runtime).

Usage (from the repo root):
    py -3 scripts/build_exe.py              # onefile: dist/devdash.exe (Windows) / dist/devdash
    py -3 scripts/build_exe.py --onedir     # folder build: faster start, easier AV whitelisting
    py -3 scripts/build_exe.py --no-gui     # CLI-only build (smaller, no pywebview bundled)

Notes:
- build ON the target OS (Windows exe on Windows, etc.) — PyInstaller binaries are
  not cross-platform;
- the exe still stores its data in DEVDASH_HOME (~/.devdash by default), so the
  binary itself is portable: replace the file to upgrade, copy ~/.devdash to migrate;
- GUI mode (`devdash gui`) needs WebView2 on Windows (preinstalled on Windows 11);
  otherwise use `devdash gui --browser`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the standalone DevDash executable.")
    ap.add_argument("--onedir", action="store_true",
                    help="folder build instead of onefile (faster start, AV-friendlier)")
    ap.add_argument("--no-gui", action="store_true",
                    help="do not bundle pywebview even if installed (CLI-only exe)")
    args = ap.parse_args()

    try:
        from PyInstaller.__main__ import run as pyinstaller_run
    except ImportError:
        sys.exit('PyInstaller is not installed: run `pip install -e ".[build]"` first.')

    spec = ROOT / "devdash.spec"
    if not spec.is_file():
        sys.exit(f"spec not found: {spec}")

    cmd = [
        str(spec),
        "--noconfirm",
        "--clean",
        f"--distpath={ROOT / 'dist'}",
        f"--workpath={ROOT / 'build'}",
    ]
    if args.onedir:
        cmd.append("--onedir")
    if args.no_gui:
        os.environ["DEVDASH_SPEC_NO_GUI"] = "1"

    pyinstaller_run(cmd)

    exe = ROOT / "dist" / ("devdash.exe" if sys.platform == "win32" else "devdash")
    if args.onedir:
        exe = ROOT / "dist" / "devdash" / ("devdash.exe" if sys.platform == "win32" else "devdash")
    print(f"\nBuilt: {exe}")
    print("Data still lives in DEVDASH_HOME (~/.devdash) — the exe is portable.")


if __name__ == "__main__":
    main()
