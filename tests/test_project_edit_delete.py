"""tests -- project edit delete"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from devdash.core import features as feats
from devdash.core import logs, sessions, tasks
from devdash.gui.api import Api, ApiError
from devdash.gui.server import make_server


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def _two_projects(api):
    a = api.projects_create({"name": "Aether Vale", "engine": "unity", "set_active": True})
    b = api.projects_create({"name": "Portfolio", "engine": "web"})
    return a, b


def test_update_name_engine_color_status(home):
    api = Api(home=home)
    a, _ = _two_projects(api)
    r = api.projects_update({"ref": a["slug"], "name": "Aether Vale 2",
                             "engine": "godot", "color": "#ff0000",
                             "status": "beta", "description": "v2"})
    assert r["name"] == "Aether Vale 2" and r["engine"] == "godot"
    assert r["color"] == "#ff0000" and r["status"] == "beta"
    assert r["slug"] == a["slug"]  # slug is the stable id, rename keeps it

    # invalid values -> 400
    with pytest.raises(ApiError) as e1:
        api.projects_update({"ref": a["slug"], "status": "nope"})
    assert e1.value.status == 400
    with pytest.raises(ApiError) as e2:
        api.projects_update({"ref": a["slug"], "color": 'red" onmouseover="x'})
    assert e2.value.status == 400  # color must be #rrggbb
    with pytest.raises(ApiError) as e3:
        api.projects_update({"ref": "ghost"})
    assert e3.value.status == 404


def test_delete_cascades_and_reassigns_active(home):
    api = Api(home=home)
    a, b = _two_projects(api)  # a is active
    # attach data to project A: task, feature, session, log
    t = api.tasks_add({"title": "Dash", "project": a["slug"], "tags": ["gameplay"]})
    api.features_add({"name": "dash", "project": a["slug"]})
    api.features_attach({"ref": "dash", "project": a["slug"], "task_id": t["id"]})
    api.session_start({"project": a["slug"], "task_id": t["id"]})
    api.session_stop({"note": "wip"})
    api.logs_add({"content": "decision", "project": a["slug"], "task_id": t["id"]})
    api.tasks_add({"title": "Landing", "project": b["slug"]})

    r = api.projects_delete({"ref": a["slug"]})
    assert r["deleted"] == a["slug"]
    assert r["active"] == b["slug"]  # focus moved to the remaining project

    projects = api.projects()
    assert [p["slug"] for p in projects["projects"]] == [b["slug"]]
    assert projects["active"] == b["slug"]
    # everything attached to A is gone; B is untouched
    assert api.tasks({"project": b["slug"]})["count"] == 1
    assert api.sessions_list({"project": b["slug"]})["count"] == 0
    assert api.logs_list({"project": "all"})["count"] == 0
    assert api.features_list({"project": "all"})["count"] == 0
    # the deletion is on the event bus (GUI live-updates)
    evs = api.events_since(0)["events"]
    assert any(e["entity"] == "project" and e["action"] == "deleted" for e in evs)


def test_delete_last_project_leaves_no_active(home):
    api = Api(home=home)
    a = api.projects_create({"name": "Solo", "set_active": True})
    r = api.projects_delete({"ref": a["id"]})
    assert r["active"] is None
    assert api.projects()["active"] is None
    assert api.status()["active"] is None


def test_delete_unknown_is_404(home):
    api = Api(home=home)
    with pytest.raises(ApiError) as e:
        api.projects_delete({"ref": "ghost"})
    assert e.value.status == 404
    with pytest.raises(ApiError) as e2:
        api.projects_delete({})
    assert e2.value.status == 400


def test_core_delete_is_transactional(home, conn=None):
    # direct core-level check: FK cascade removes children, meta pointer fixed
    import os
    from devdash.core import db, projects as proj
    h = home
    c = db.connect(h / "devdash.db")
    db.init_db(c)
    p1 = proj.create(c, "One", engine="unity")
    p2 = proj.create(c, "Two", engine="web")
    proj.set_active(c, p1.id)
    tasks.add(c, p1.id, "t1")
    logs.add(c, "note", project_id=p1.id)
    sessions.start(c, p1.id)
    sessions.stop(c)
    proj.delete(c, p1.id)
    assert proj.get(c, p1.id) is None
    assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM log_entries").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert proj.get_active(c).id == p2.id
    c.close()


def test_http_update_and_delete_flow(home):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def post(path, body, host=None):
        headers = {"Content-Type": "application/json"}
        if host:
            headers["Host"] = host
        return urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                      headers=headers, method="POST")

    try:
        a = json.loads(urllib.request.urlopen(post("/api/projects", {"name": "Game", "engine": "unity", "set_active": True})).read())
        json.loads(urllib.request.urlopen(post("/api/projects", {"name": "Site", "engine": "web"})).read())
        # update
        r = json.loads(urllib.request.urlopen(post("/api/projects/update",
                                                   {"ref": a["slug"], "name": "Game 2", "engine": "godot"})).read())
        assert r["name"] == "Game 2" and r["engine"] == "godot"
        # delete via HTTP
        d = json.loads(urllib.request.urlopen(post("/api/projects/delete", {"ref": a["slug"]})).read())
        assert d["deleted"] == a["slug"] and d["active"] == "site"
        # deleting again -> 404
        try:
            urllib.request.urlopen(post("/api/projects/delete", {"ref": a["slug"]}))
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404
        # forged Host -> 403 even on delete
        try:
            urllib.request.urlopen(post("/api/projects/delete", {"ref": "site"}, host="evil.example.com"))
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 403
        # project list no longer contains it
        plist = json.loads(urllib.request.urlopen(base + "/api/projects").read())
        assert [p["slug"] for p in plist["projects"]] == ["site"]
    finally:
        srv.shutdown()
        srv.server_close()
