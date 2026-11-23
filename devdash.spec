# PyInstaller spec for a standalone DevDash executable.
# Built via `scripts/build_exe.py` (which just calls PyInstaller with this spec).
#
# - ONEDIR by default -> dist/devdash/devdash.exe : a folder of individually
#   scannable files. Antivirus products flag self-extracting ONEfile binaries
#   far more often; set DEVDASH_ONEFILE=1 (or `build_exe.py --onefile`) to opt
#   back into a single file.
# - UPX disabled: packed binaries trigger AV heuristics.
# - Windows metadata: scripts/version_info.txt (Explorer > Properties) and
#   scripts/app.manifest (asInvoker, DPI-aware, common-controls v6) make the exe
#   behave like a normal desktop app. Sign with scripts/sign_exe.ps1 to remove
#   SmartScreen warnings with a trusted certificate.
# - bundles devdash package data (core/schema.sql)
# - bundles pywebview ONLY if it is installed and DEVDASH_SPEC_NO_GUI != "1"
#   (use --no-gui for a smaller CLI-only build)
# - DEVDASH_CONSOLE=0 builds a windowed exe (no console flash) for double-click GUI use

from PyInstaller.utils.hooks import collect_data_files, collect_all

import os
import sys

# package data: core/schema.sql must ship inside the exe
datas = collect_data_files("devdash")
hiddenimports = ["typer", "rich", "rich.console", "rich.table", "rich.panel"]
binaries = []

if os.environ.get("DEVDASH_SPEC_NO_GUI") != "1":
    try:
        import webview  # noqa: F401  (optional [gui] extra)
        _w_data, _w_binaries, _w_hidden = collect_all("webview")
        datas += _w_data
        binaries += _w_binaries
        hiddenimports += _w_hidden
    except ImportError:
        pass  # CLI-only build: `devdash gui` will suggest --browser

ONEFILE = os.environ.get("DEVDASH_ONEFILE") == "1"
CONSOLE = os.environ.get("DEVDASH_CONSOLE", "1") != "0"
IS_WIN = sys.platform == "win32"

# Windows legitimacy metadata (no-op on other platforms)
version_file = os.path.join(SPECPATH, "scripts", "version_info.txt")
manifest_file = os.path.join(SPECPATH, "scripts", "app.manifest")
exe_meta = {}
if IS_WIN:
    if os.path.isfile(version_file):
        exe_meta["version"] = version_file
    if os.path.isfile(manifest_file):
        exe_meta["manifest"] = manifest_file

a = Analysis(
    ["devdash/__main__.py"],   # same entry point as `python -m devdash`
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="devdash",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,          # AV false positives with UPX-packed onefile
        console=CONSOLE,
        **exe_meta,
    )
else:
    # onedir (default): plain files on disk, nothing self-extracting at launch
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="devdash",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=CONSOLE,
        **exe_meta,
    )
    COLLECT(
        exe,
        a.binaries,
        a.datas,
        name="devdash",
    )
