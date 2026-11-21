# PyInstaller spec for a standalone DevDash executable.
# Built via `scripts/build_exe.py` (which just calls PyInstaller with this spec).
#
# - onefile by default -> dist/devdash(.exe) : a single double-clickable file
# - bundles devdash package data (core/schema.sql)
# - bundles pywebview ONLY if it is installed and DEVDASH_SPEC_NO_GUI != "1"
#   (use --no-gui for a smaller CLI-only build)
# - UPX disabled: avoids antivirus false positives on packed binaries

from PyInstaller.utils.hooks import collect_data_files, collect_all

import os

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
    console=True,       # CLI executable; `devdash gui` opens the native window from it
)
