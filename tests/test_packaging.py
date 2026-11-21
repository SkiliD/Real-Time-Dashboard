"""Packaging tests: the standalone-executable build must stay wired correctly.

We do NOT run PyInstaller here (heavy); we assert the packaging contract:
entry point, package data (schema.sql), build extra, spec file, and that
`python -m devdash` (the exe's entry) works.
"""

from __future__ import annotations

import importlib.resources
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


def test_build_script_exists_and_is_runnable_help():
    script = ROOT / "scripts" / "build_exe.py"
    assert script.is_file()
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0
    assert "--onedir" in proc.stdout and "--no-gui" in proc.stdout


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
