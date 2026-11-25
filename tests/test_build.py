# tests -- build the standalone exe: core build module (PyInstaller subprocess,
# background runner for the GUI), CLI `devdash build`, GUI routes + wiring.

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import build as build_core
from devdash.core import i18n
from devdash.gui.api import Api, ApiError
from devdash.gui.server import GET_ROUTES, POST_ROUTES

runner = CliRunner()

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "devdash/gui/static/index.html").read_text(encoding="utf-8")
JS = (ROOT / "devdash/gui/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def _fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    (root / "devdash.spec").write_text("# fake spec\n", encoding="utf-8")
    return root


def test_exe_path_onedir_vs_onefile(tmp_path: Path):
    root = _fake_root(tmp_path)
    name = "devdash.exe" if sys.platform == "win32" else "devdash"
    assert build_core.exe_path(root, onefile=False) == root / "dist" / "devdash" / name
    assert build_core.exe_path(root, onefile=True) == root / "dist" / name


def test_build_exe_missing_spec(tmp_path: Path):
    r = build_core.build_exe(root=tmp_path / "nope")
    assert r["ok"] is False and "spec not found" in r["error"]


def test_build_exe_missing_pyinstaller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _fake_root(tmp_path)
    monkeypatch.setattr(build_core, "pyinstaller_available", lambda: False)
    r = build_core.build_exe(root=root)
    assert r["ok"] is False and "PyInstaller" in r["error"]


def test_build_exe_runs_pyinstaller_with_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _fake_root(tmp_path)
    monkeypatch.setattr(build_core, "pyinstaller_available", lambda: True)
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw["env"]
        captured["cwd"] = kw["cwd"]
        exe = build_core.exe_path(root, onefile=False)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ fake")
        return subprocess.CompletedProcess(cmd, 0, stdout="built ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = build_core.build_exe(onefile=False, no_gui=True, root=root)
    assert r["ok"] is True and r["exe"] == str(build_core.exe_path(root))
    assert captured["cmd"][1:3] == ["-m", "PyInstaller"]
    assert str(root / "devdash.spec") in captured["cmd"]
    assert "--noconfirm" in captured["cmd"] and "--clean" in captured["cmd"]
    assert captured["env"]["DEVDASH_SPEC_NO_GUI"] == "1"
    assert "DEVDASH_ONEFILE" not in captured["env"]
    assert captured["cwd"] == root
    assert "built ok" in r["log_tail"]


def test_build_exe_onefile_env_and_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _fake_root(tmp_path)
    monkeypatch.setattr(build_core, "pyinstaller_available", lambda: True)
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["env"] = kw["env"]
        exe = build_core.exe_path(root, onefile=True)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ fake")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = build_core.build_exe(onefile=True, root=root)
    assert r["ok"] is True
    assert captured["env"]["DEVDASH_ONEFILE"] == "1"
    assert r["exe"] == str(build_core.exe_path(root, onefile=True))


def test_build_exe_failure_rc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _fake_root(tmp_path)
    monkeypatch.setattr(build_core, "pyinstaller_available", lambda: True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom"),
    )
    r = build_core.build_exe(root=root)
    assert r["ok"] is False and r["exe"] is None and "code 2" in r["error"]
    assert "boom" in r["log_tail"]


def test_start_build_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(build_core, "pyinstaller_available", lambda: True)
    entered = threading.Event()

    def fake_build_exe(**kw):
        entered.set()
        time.sleep(0.2)  # stay "running" long enough to observe the busy state
        return {"ok": True, "exe": "/x/devdash.exe", "log_tail": "log", "error": None}

    monkeypatch.setattr(build_core, "build_exe", fake_build_exe)
    with build_core._lock:
        build_core._state.update(running=False, ok=None, exe=None, error=None, log_tail="")
    assert build_core.start_build() == {"started": True}
    assert entered.wait(2)
    assert build_core.build_status()["running"] is True
    # a second start while running must be refused
    r2 = build_core.start_build()
    assert r2["started"] is False and "already running" in r2["error"]
    for _ in range(50):
        s = build_core.build_status()
        if not s["running"]:
            break
        time.sleep(0.05)
    s = build_core.build_status()
    assert s["running"] is False and s["ok"] is True
    assert s["exe"] == "/x/devdash.exe" and s["log_tail"] == "log"


def test_start_build_without_pyinstaller(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(build_core, "pyinstaller_available", lambda: False)
    r = build_core.start_build()
    assert r["started"] is False and "PyInstaller" in r["error"]


def test_cli_build_help():
    r = runner.invoke(app, ["build", "--help"])
    assert r.exit_code == 0
    assert "--onefile" in r.output and "--no-gui" in r.output and "--no-console" in r.output


def test_cli_build_reports_missing_pyinstaller(monkeypatch: pytest.MonkeyPatch, home: Path):
    monkeypatch.setattr(build_core, "pyinstaller_available", lambda: False)
    r = runner.invoke(app, ["build"])
    assert r.exit_code == 0
    assert "PyInstaller" in r.output


def test_api_routes_registered():
    assert "/api/build/status" in GET_ROUTES
    assert "/api/build/run" in POST_ROUTES


def test_api_build_status_and_run(home: Path, monkeypatch: pytest.MonkeyPatch):
    api = Api(home=home)
    s = api.build_status()
    assert s["running"] is False
    # mutating route: two-step confirm required, like /api/update
    with pytest.raises(ApiError):
        api.build_run({})
    monkeypatch.setattr(build_core, "pyinstaller_available", lambda: False)
    r = api.build_run({"confirm": True})
    assert r["started"] is False and "PyInstaller" in r["error"]


def test_gui_build_card_present():
    assert 'id="btn-build"' in HTML and 'id="build-status"' in HTML and 'id="build-log"' in HTML
    assert 'id="build-onefile"' in HTML
    for key in ("gui.build.title", "gui.build.desc", "gui.build.btn", "gui.build.onefile"):
        assert key in i18n.FR and key in i18n.EN, key


def test_gui_build_wired_in_appjs():
    assert '$("#btn-build").onclick' in JS
    assert 'api.post("/api/build/run"' in JS
    assert 'api.get("/api/build/status")' in JS


def test_i18n_parity_after_build_keys():
    assert set(i18n.FR) == set(i18n.EN)
