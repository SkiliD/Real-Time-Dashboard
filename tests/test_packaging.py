# tests -- packaging
# Read-only on purpose.

from __future__ import annotations

import importlib.resources
import struct
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_schema_sql_is_package_data():
    # the exe bundles package data via collect_data_files("devdash"):
    # schema.sql must be visible as a package resource
    res = importlib.resources.files("devdash.core").joinpath("schema.sql")
    assert res.is_file()
    text = res.read_text(encoding="utf-8")
    assert "CREATE TABLE" in text


def test_pyproject_declares_script_and_build_extra():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["devdash"] == "devdash.cli.main:entrypoint"
    extras = data["project"]["optional-dependencies"]
    assert "pyinstaller" in extras["build"][0]
    assert "pywebview" in extras["gui"][0]
    # schema.sql must be declared as package data for installed wheels
    assert "core/schema.sql" in data["tool"]["setuptools"]["package-data"]["devdash"]


def test_pyinstaller_spec_is_wired():
    spec = (ROOT / "devdash.spec").read_text(encoding="utf-8")
    assert '"devdash/__main__.py"' in spec or "'devdash/__main__.py'" in spec
    assert "collect_data_files" in spec  # schema.sql bundled into the exe
    assert "upx=False" in spec


def test_gui_icon_is_package_data_and_valid_ico():
    # the window/taskbar icon must ship inside installed wheels
    res = importlib.resources.files("devdash.gui").joinpath("icon.ico")
    assert res.is_file()
    raw = res.read_bytes()
    reserved, typ, count = struct.unpack("<HHH", raw[:6])
    assert reserved == 0 and typ == 1 and count >= 4, "not a valid multi-size ICO"
    assert len(raw) > 5000
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "icon.ico" in data["tool"]["setuptools"]["package-data"]["devdash.gui"]


def test_spec_embeds_exe_icon():
    # running the frozen devdash.exe must show the app icon in the taskbar too
    spec = (ROOT / "devdash.spec").read_text(encoding="utf-8")
    assert "icon.ico" in spec and 'exe_meta["icon"]' in spec


def test_spec_is_av_friendly():
    # onedir by default (no self-extracting onefile), Windows metadata + manifest wired
    spec = (ROOT / "devdash.spec").read_text(encoding="utf-8")
    assert "DEVDASH_ONEFILE" in spec and "exclude_binaries=True" in spec  # onedir default
    assert "version_info.txt" in spec and "app.manifest" in spec
    assert "COLLECT(" in spec


def test_windows_version_metadata_present():
    v = (ROOT / "scripts" / "version_info.txt").read_text(encoding="utf-8")
    for field in ["ProductName", "FileDescription", "CompanyName", "LegalCopyright",
                  "OriginalFilename", "FileVersion"]:
        assert field in v, field
    assert "DevDash" in v


def test_app_manifest_is_as_invoker_and_dpi_aware():
    m = (ROOT / "scripts" / "app.manifest").read_text(encoding="utf-8")
    assert 'level="asInvoker"' in m          # no UAC prompt for a local tool
    assert "PerMonitorV2" in m               # crisp GUI on high-DPI
    assert "Common-Controls" in m


def test_sign_script_exists():
    s = (ROOT / "scripts" / "sign_exe.ps1").read_text(encoding="utf-8")
    assert "signtool" in s and "sign" in s and "/fd" in s


def test_readmes_document_smart_screen():
    for f in ["README.md", "README.fr.md"]:
        txt = (ROOT / f).read_text(encoding="utf-8")
        low = txt.lower()
        assert "smartscreen" in low and "sign_exe.ps1" in txt, f


def test_build_script_exists_and_is_runnable_help():
    script = ROOT / "scripts" / "build_exe.py"
    assert script.is_file()
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0
    # onedir is now the DEFAULT (AV-friendlier); --onefile opts back in
    assert "--onefile" in proc.stdout and "--no-gui" in proc.stdout and "--no-console" in proc.stdout


def test_python_m_devDash_entrypoint_works():
    # the exe uses the same entry point as `python -m devdash`
    import os
    env = dict(os.environ, PYTHONPATH=str(ROOT))  # keep the full env (user site-packages)
    proc = subprocess.run(
        [sys.executable, "-m", "devdash", "version"],
        capture_output=True, text=True, cwd=ROOT, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("devdash")


def test_version_is_a_coherent_beta_prerelease():
    # one source of truth: pyproject == devdash.__version__; a beta must be a
    # PEP 440 pre-release (e.g. 0.9.0b1 = "beta 1 of 0.9.0", sorts before 0.9.0)
    from packaging.version import Version
    import devdash
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    v = Version(devdash.__version__)
    assert pyproject["project"]["version"] == devdash.__version__
    assert v.is_prerelease and v.base_version == "0.9.0" and v.pre == ("b", 1)
    # Windows metadata: FileVersion is 4 numeric parts (0.9.0.1), ProductVersion carries the label
    vi = (ROOT / "scripts" / "version_info.txt").read_text(encoding="utf-8")
    assert "StringStruct('FileVersion', '0.9.0.1')" in vi
    assert "StringStruct('ProductVersion', '0.9.0-beta.1')" in vi
    assert 'assemblyIdentity version="0.9.0.1"' in (ROOT / "scripts" / "app.manifest").read_text(encoding="utf-8")
