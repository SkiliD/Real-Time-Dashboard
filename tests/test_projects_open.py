# tests -- connected project folder + "open in OS file explorer"
# (projects.connect stores the folder; `projects open` / POST /api/projects/open /
#  devdash_projects_open open it. The explorer launch is monkeypatched in tests.)
# Works on Windows, Mac and Linux.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import config as cfgmod, db, projects as proj, sysopen
from devdash.gui import server
from devdash.gui.api import Api, ApiError
from devdash.mcp_server import TOOLS, _call

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home):
    c = db.connect(cfgmod.db_path(home))
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def api(home) -> Api:
    a = Api(home=home)
    a.projects_create({"name": "Game", "engine": "unity", "set_active": True})
    return a


@pytest.fixture()
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record explorer opens instead of launching a real process."""
    calls: list[str] = []
    monkeypatch.setattr(sysopen, "open_in_explorer", lambda p: calls.append(p))
    return calls


# --- core: path storage + validation --------------------------------------------

def test_schema_has_path_column(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    assert "path" in cols
    p = proj.create(conn, "NoFolder")
    assert p.path == ""


def test_create_update_path_validation(conn, tmp_path):
    p = proj.create(conn, "WithFolder", path=str(tmp_path))
    assert Path(p.path) == tmp_path.resolve()
    # nonexistent / non-string paths are refused
    with pytest.raises(ValueError):
        proj.create(conn, "Bad", path=str(tmp_path / "nope"))
    with pytest.raises(ValueError):
        proj.create(conn, "Bad2", path=123)  # type: ignore[arg-type]
    # update: set, replace, clear
    other = tmp_path / "other"
    other.mkdir()
    p = proj.update(conn, p.id, path=str(other))
    assert Path(p.path) == other.resolve()
    p = proj.update(conn, p.id, path="")
    assert p.path == ""
    with pytest.raises(ValueError):
        proj.update(conn, p.id, path=str(tmp_path / "gone"))


# --- GUI API --------------------------------------------------------------------

def test_route_registered():
    assert "/api/projects/open" in server.POST_ROUTES


def test_api_connect_stores_path(api, tmp_path):
    r = api.projects_connect({"path": str(tmp_path), "no_git": True, "name": "Connected"})
    assert Path(r["project"]["path"]) == tmp_path.resolve()


def test_api_projects_open_requires_folder(api, opened):
    with pytest.raises(ApiError) as ei:
        api.projects_open({})
    assert ei.value.status == 400 and "no connected folder" in str(ei.value)


def test_api_projects_open_ok_and_gone(api, tmp_path, opened):
    api.projects_update({"ref": "game", "path": str(tmp_path)})
    r = api.projects_open({})
    assert r["project"] == "game" and Path(r["opened"]) == tmp_path.resolve()
    assert opened == [r["opened"]]
    # folder deleted afterwards -> clean 400, nothing opened
    gone = tmp_path / "sub"
    gone.mkdir()
    api.projects_update({"ref": "game", "path": str(gone)})
    gone.rmdir()
    with pytest.raises(ApiError) as ei:
        api.projects_open({})
    assert ei.value.status == 400 and "no longer exists" in str(ei.value)
    assert opened == [r["opened"]]  # no extra open


def test_git_open_uses_sysopen(api, git_repo, opened):
    api.git_link({"path": str(git_repo)})
    r = api.git_open({})
    assert r["opened"] in opened


# --- MCP ------------------------------------------------------------------------

def test_mcp_projects_open(home, conn, tmp_path, opened):
    p = proj.create(conn, "Mcp", path=str(tmp_path))
    proj.set_active(conn, p.id)
    assert any(t["name"] == "devdash_projects_open" for t in TOOLS)
    r = _call("devdash_projects_open", {}, home)
    assert r["project"] == p.slug and Path(r["opened"]) == tmp_path.resolve()
    assert opened == [r["opened"]]


def test_mcp_projects_open_without_folder(home, conn, opened):
    p = proj.create(conn, "Bare")
    proj.set_active(conn, p.id)
    with pytest.raises(KeyError):
        _call("devdash_projects_open", {}, home)
    assert opened == []


# --- CLI ------------------------------------------------------------------------

def test_cli_projects_open(home, tmp_path, opened):
    r = runner.invoke(app, ["--json", "projects", "create", "Solo", "--set-active"])
    assert r.exit_code == 0, r.output
    # no connected folder -> clean error
    r = runner.invoke(app, ["projects", "open"])
    assert r.exit_code != 0 and "dossier connecté" in r.output
    # connect a real folder, then open it by slug
    r = runner.invoke(app, ["--json", "projects", "connect", str(tmp_path),
                            "--no-git", "--name", "Connected"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["--json", "projects", "open", "connected"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["opened"] and Path(data["opened"]) == tmp_path.resolve()
    assert opened == [data["opened"]]


# --- linking an EXISTING project to a folder from the GUI edit modal -----------

def test_edit_modal_has_folder_field_and_wiring():
    """The project edit modal must let the user set/clear the connected folder
    (that's what makes the 📂 open-in-explorer button appear)."""
    root = Path(__file__).resolve().parents[1]
    html = (root / "devdash/gui/static/index.html").read_text(encoding="utf-8")
    js = (root / "devdash/gui/static/app.js").read_text(encoding="utf-8")
    modal = html[html.index('id="modal-edit"'):html.index('id="modal-feat"')]
    assert 'id="e-path"' in modal, "edit modal needs the folder input"
    assert 'id="btn-browse-epath"' in modal, "edit modal needs the browse button"
    assert 'data-i18n="gui.edit.path"' in modal
    # app.js: fill on open, send on save, browse button wired to the OS picker
    assert '$("#e-path").value = p.path || "";' in js
    body = js[js.index("async function saveEditProject"):]
    body = body[:body.index("\nasync function ")]
    assert 'path: $("#e-path").value.trim()' in body
    assert 'browsePathInto("#e-path", "gui.pick_folder")' in js
    from devdash.core import i18n
    for k in ("gui.edit.path", "gui.edit.path_tt"):
        assert k in i18n.FR and k in i18n.EN


def test_update_existing_project_path_via_api(api, tmp_path):
    """POST /api/projects/update with path= links the folder (existing dir,
    stored resolved); empty string clears it; a non-directory is a clean 400."""
    folder = tmp_path / "my-game"
    folder.mkdir()
    p = api.projects()["projects"][0]
    assert p.get("path", "") == ""
    r = api.projects_update({"ref": p["slug"], "path": str(folder)})
    assert r["path"] == str(folder.resolve())
    # clearing works with an empty string
    r = api.projects_update({"ref": p["slug"], "path": "  "})
    assert r["path"] == ""
    # non-existing directory -> 400, previous value untouched semantics
    api.projects_update({"ref": p["slug"], "path": str(folder)})
    with pytest.raises(ApiError) as e:
        api.projects_update({"ref": p["slug"], "path": str(tmp_path / "nope")})
    assert e.value.status == 400
    assert api.projects()["projects"][0]["path"] == str(folder.resolve())
