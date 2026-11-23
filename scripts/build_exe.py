# pyinstaller wrapper, onedir by default
# Read-only on purpose.

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the standalone DevDash executable.")
    ap.add_argument("--onefile", action="store_true",
                    help="single-file build instead of the default onedir folder "
                         "(more AV heuristics on onefile self-extractors)")
    ap.add_argument("--no-gui", action="store_true",
                    help="do not bundle pywebview even if installed (CLI-only exe)")
    ap.add_argument("--no-console", action="store_true",
                    help="windowed exe (no console window) — for double-click GUI use")
    args = ap.parse_args()

    try:
        from PyInstaller.__main__ import run as pyinstaller_run
    except ImportError:
        sys.exit('PyInstaller is not installed: run `pip install -e ".[build]"` first.')

    spec = ROOT / "devdash.spec"
    if not spec.is_file():
        sys.exit(f"spec not found: {spec}")

    if args.onefile:
        os.environ["DEVDASH_ONEFILE"] = "1"
    if args.no_gui:
        os.environ["DEVDASH_SPEC_NO_GUI"] = "1"
    if args.no_console:
        os.environ["DEVDASH_CONSOLE"] = "0"

    cmd = [
        str(spec),
        "--noconfirm",
        "--clean",
        f"--distpath={ROOT / 'dist'}",
        f"--workpath={ROOT / 'build'}",
    ]

    pyinstaller_run(cmd)

    exe = ROOT / "dist" / ("devdash.exe" if sys.platform == "win32" else "devdash")
    if not args.onefile:
        exe = ROOT / "dist" / "devdash" / ("devdash.exe" if sys.platform == "win32" else "devdash")
    print(f"\nBuilt: {exe}")
    print("Data still lives in DEVDASH_HOME (~/.devdash) — the exe is portable.")
    if sys.platform == "win32" and not os.environ.get("DEVDASH_SIGNED"):
        print("\nSmartScreen: unsigned exe -> 'More info > Run anyway' the first time.")
        print("To sign it (removes the warning with a trusted cert): "
              f'  .\\scripts\\sign_exe.ps1 -ExePath "{exe}"')


if __name__ == "__main__":
    main()
