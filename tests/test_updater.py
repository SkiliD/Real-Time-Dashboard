"""Self-update: `devdash update` / /api/update/* — git status, check (fetch+compare),
ff-only pull, dirty-tree abort, dependency reinstall skip. Uses LOCAL git remotes
(file paths), so no network is involved.
"""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import updater
from devdash.gui.api import Api, ApiError
from devdash.gui.server import make_server

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], check=True,
                       capture_output=True, text=True)
    return r.stdout.strip()


def _commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"add {name}")
    return _git(repo, "rev-parse", "--short", "HEAD")


@pytest.fixture()
def remote_and_clone(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Returns (bare remote, working clone used to push, installed clone)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    _git(work, "config", "user.email", "t@t.local")
    _git(work, "config", "user.name", "T")
    _git(work, "config", "commit.gpgsign", "false")
    _commit(work, "README.md", "v1")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-q", "-u", "origin", "main")
    clone = tmp_path / "installed"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)  # clone sets upstream
    _git(clone, "config", "user.email", "t@t.local")
    _git(clone, "config", "user.name", "T")
    return remote, work, clone


# --- core ---------------------------------------------------------------------

def test_git_status_readonly(remote_and_clone):
    _, _, clone = remote_and_clone
    st = updater.git_status(clone)
    assert st["git"] is True and st["head"] and st["branch"] == "main"
    assert st["dirty"] is False and "remote.git" in st["remote"]
    # non-repo -> guidance mode, no crash
    st2 = updater.git_status(clone.parent / "nowhere")
    assert st2["git"] is False


def test_check_then_update_flow(remote_and_clone):
    _, work, clone = remote_and_clone
    # nothing new yet
    r = updater.check_for_updates(clone)
    assert r["update_available"] is False and r["behind"] == 0
    # push a new commit from the "other machine"
    new_head = _commit(work, "feature.txt", "v2")
    _git(work, "push", "-q", "origin", "main")
    r = updater.check_for_updates(clone)  # fetches from the LOCAL path remote
    assert r["update_available"] is True and r["behind"] == 1
    assert r["remote"] == new_head
    # apply the update (no pip reinstall in tests)
    res = updater.update(clone, reinstall=False)
    assert res["ok"] is True and res["restart_needed"] is True
    assert res["head"] == new_head
    assert any("pull" in s for s in res["steps"])
    assert (clone / "feature.txt").exists()
    # already up to date now
    r = updater.check_for_updates(clone)
    assert r["update_available"] is False


def test_update_aborts_on_dirty_tree(remote_and_clone):
    _, work, clone = remote_and_clone
    _commit(work, "x.txt", "v2")
    _git(work, "push", "-q", "origin", "main")
    (clone / "uncommitted.txt").write_text("dirty")
    res = updater.update(clone, reinstall=False)
    assert res["ok"] is False and "uncommitted" in res["error"]
    assert not (clone / "x.txt").exists()  # nothing was pulled


def test_update_non_repo_guidance(tmp_path):
    res = updater.update(tmp_path)  # not a git repo
    assert res["ok"] is False and res["mode"] == "pip" and "pip" in res["error"]


def test_install_mode_detection(remote_and_clone):
    _, _, clone = remote_and_clone
    monkey = pytest.MonkeyPatch()
    monkey.setattr(updater, "package_repo", lambda: clone)
    assert updater.install_mode() == "git"
    monkey.setattr(updater, "package_repo", lambda: None)
    assert updater.install_mode() == "pip"
    monkey.undo()


# --- CLI -----------------------------------------------------------------------

def test_cli_update_status_check_and_pull(remote_and_clone, monkeypatch):
    _, work, clone = remote_and_clone
    monkeypatch.setattr(updater, "package_repo", lambda: clone)

    r = runner.invoke(app, ["--json", "update", "status"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["git"] is True

    _commit(work, "v2.txt", "v2")
    _git(work, "push", "-q", "origin", "main")

    r = runner.invoke(app, ["--json", "update", "check"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["update_available"] is True

    r = runner.invoke(app, ["--json", "update", "--no-reinstall"])
    assert r.exit_code == 0, r.output
    res = json.loads(r.output)
    assert res["ok"] is True and (clone / "v2.txt").exists()


# --- HTTP API -------------------------------------------------------------------

def test_api_update_endpoints(home, remote_and_clone, monkeypatch):
    _, work, clone = remote_and_clone
    monkeypatch.setattr(updater, "package_repo", lambda: clone)
    api = Api(home=home)

    st = api.update_status()  # read-only
    assert st["version"] and st["git"] is True and st["head"]

    r = api.update_check({})
    assert r["update_available"] is False

    _commit(work, "api.txt", "v2")
    _git(work, "push", "-q", "origin", "main")
    r = api.update_check({})
    assert r["update_available"] is True

    # without confirm=true the mutating endpoint refuses (two-step in the GUI)
    with pytest.raises(ApiError):
        api.update_now({"reinstall": False})
    res = api.update_now({"reinstall": False, "confirm": True})
    assert res["ok"] is True and (clone / "api.txt").exists()

    # HTTP level
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        s = json.loads(urllib.request.urlopen(base + "/api/update/status").read())
        assert s["git"] is True and s["version"]
        req = urllib.request.Request(base + "/api/update/check", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        c = json.loads(urllib.request.urlopen(req).read())
        assert c["update_available"] is False  # already pulled above
    finally:
        srv.shutdown()
        srv.server_close()
