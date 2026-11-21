"""Connect DevDash to an EXISTING project folder: engine auto-detection, git root
discovery (walk up), `projects connect` (CLI), /api/detect + /api/projects/connect.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import detect, gitwatch
from devdash.gui.api import Api, ApiError
from devdash.gui.server import make_server

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def _mk_engine_dir(base: Path, engine: str) -> Path:
    d = base / f"game-{engine}"
    if engine == "unity":
        (d / "ProjectSettings").mkdir(parents=True)
        (d / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0\n")
    elif engine == "godot":
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.godot").write_text("config_version=5\n")
    elif engine == "unreal":
        d.mkdir(parents=True, exist_ok=True)
        (d / "Game.uproject").write_text("{}")
    elif engine == "web":
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.json").write_text("{}")
    else:
        d.mkdir(parents=True, exist_ok=True)
    return d


# --- core detection -----------------------------------------------------------

@pytest.mark.parametrize("engine", ["unity", "godot", "unreal", "web", "other"])
def test_detect_engine(tmp_path, engine):
    d = _mk_engine_dir(tmp_path, engine)
    assert detect.detect_engine(d) == engine
    info = detect.detect_project(str(d))
    assert info["is_dir"] and info["engine"] == engine
    assert info["name"] == f"Game {engine}"  # folder-derived, case preserved


def test_detect_missing_and_file(tmp_path):
    assert detect.detect_project(str(tmp_path / "nope"))["is_dir"] is False
    f = tmp_path / "file.txt"
    f.write_text("x")
    info = detect.detect_project(str(f))
    assert info["is_dir"] is False


def test_find_git_root_walks_up(tmp_path, git_repo):
    # probe a deep subfolder of a git repo -> root found by walking up
    deep = git_repo / "Assets" / "Scripts"
    deep.mkdir(parents=True)
    assert detect.find_git_root(deep) == git_repo.resolve()
    assert detect.detect_project(str(deep))["git_root"] == str(git_repo.resolve())
    # outside any repo -> None
    assert detect.find_git_root(tmp_path / "home") is None


# --- API -----------------------------------------------------------------------

def test_api_detect_path(home, tmp_path):
    api = Api(home=home)
    d = _mk_engine_dir(tmp_path, "unity")
    r = api.detect_path({"path": str(d)})
    assert r["is_dir"] and r["engine"] == "unity" and r["name"]
    assert api.detect_path({"path": str(tmp_path / "nope")})["is_dir"] is False
    with pytest.raises(ApiError):
        api.detect_path({"path": "x" * 5000})


def test_api_projects_connect_links_git(home, git_repo):
    api = Api(home=home)
    game = git_repo / "MyUnityGame"
    (game / "ProjectSettings").mkdir(parents=True)
    (game / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0\n")

    r = api.projects_connect({"path": str(game), "set_active": True})
    p = r["project"]
    assert p["engine"] == "unity" and p["name"] == "MyUnityGame"
    assert r["git_root"] == str(git_repo.resolve())
    assert api.projects()["active"] == p["slug"]
    # commits were synced at connect time (read-only git)
    commits = api.git_commits({"project": p["slug"]})
    assert commits["count"] >= 3
    st = api.git_status({"project": p["slug"]})
    assert st["linked"] and st["branch"]


def test_api_projects_connect_no_git_and_errors(home, tmp_path):
    api = Api(home=home)
    d = _mk_engine_dir(tmp_path, "godot")
    r = api.projects_connect({"path": str(d), "no_git": True, "name": "Custom Name"})
    assert r["project"]["name"] == "Custom Name" and r["project"]["engine"] == "godot"
    assert r["git_root"] is None
    with pytest.raises(ApiError) as e:
        api.projects_connect({"path": str(tmp_path / "nope")})
    assert e.value.status == 400
    with pytest.raises(ApiError):
        api.projects_connect({})


# --- CLI ------------------------------------------------------------------------

def test_cli_projects_connect(home, git_repo):
    game = git_repo / "WebGame"
    game.mkdir()
    (game / "package.json").write_text("{}")
    r = runner.invoke(app, ["--json", "projects", "connect", str(game), "--set-active"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["project"]["engine"] == "web" and data["git_root"] == str(git_repo.resolve())
    r = runner.invoke(app, ["--json", "projects", "list"])
    assert json.loads(r.output)["active"] == data["project"]["slug"]
    # bad path -> clean error, non-zero exit
    r = runner.invoke(app, ["projects", "connect", str(home / "nope")])
    assert r.exit_code != 0


# --- HTTP -----------------------------------------------------------------------

def test_http_detect_and_connect(home, git_repo):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        game = git_repo / "GodotGame"
        game.mkdir()
        (game / "project.godot").write_text("config_version=5\n")
        d = json.loads(urllib.request.urlopen(
            base + "/api/detect?path=" + urllib.request.quote(str(game))).read())
        assert d["engine"] == "godot" and d["git_root"] == str(git_repo.resolve())

        req = urllib.request.Request(
            base + "/api/projects/connect",
            data=json.dumps({"path": str(game), "set_active": True}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        r = json.loads(urllib.request.urlopen(req).read())
        assert r["project"]["engine"] == "godot" and r["git_root"]
        # project really has the git link + commits
        st = json.loads(urllib.request.urlopen(
            base + "/api/git/status?project=" + r["project"]["slug"]).read())
        assert st["linked"] is True
    finally:
        srv.shutdown()
        srv.server_close()
