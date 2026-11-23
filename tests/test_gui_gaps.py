from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from devdash.core import db, logs, projects as proj, tasks as tsk
from devdash.gui.api import Api, ApiError
from devdash.gui.server import make_server


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def api(home) -> Api:
    a = Api(home=home)
    a.projects_create({"name": "Game", "engine": "unity", "set_active": True})
    return a


# --- task edit / search / subtasks ------------------------------------------

def test_tasks_update_all_fields_and_clears(api):
    t = api.tasks_add({"title": "Dash", "priority": "high", "tags": ["gameplay"], "estimate_min": 90})
    f = api.features_add({"name": "dash"})
    parent = api.tasks_add({"title": "Movement"})
    r = api.tasks_update({"id": t["id"], "title": "Air dash", "description": "in the air",
                          "priority": "critical", "tags": ["gameplay", "air"],
                          "estimate_min": 120, "parent_id": parent["id"], "feature_id": f["id"]})
    assert r["title"] == "Air dash" and r["priority"] == "critical" and r["estimate_min"] == 120
    assert r["tags"] == ["gameplay", "air"] and r["parent_id"] == parent["id"] and r["feature_id"] == f["id"]
    # validation (cycle check while the parent link still exists)
    with pytest.raises(ApiError) as e1:
        api.tasks_update({"id": t["id"], "priority": "urgent"})
    assert e1.value.status == 400
    with pytest.raises(ApiError) as e2:
        api.tasks_update({"id": 999, "title": "x"})
    assert e2.value.status == 404
    with pytest.raises(ApiError) as e3:
        api.tasks_update({"id": t["id"], "tags": "notalist"})
    assert e3.value.status == 400
    with pytest.raises(ApiError) as e4:  # parent cycle: parent under its own child
        api.tasks_update({"id": parent["id"], "parent_id": t["id"]})
    assert e4.value.status == 400
    # explicit null clears parent/feature (the _UNSET sentinel: None means NULL)
    r2 = api.tasks_update({"id": t["id"], "parent_id": None, "feature_id": None})
    assert r2["parent_id"] is None and r2["feature_id"] is None


def test_tasks_search_q(home):
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    p = proj.create(c, "Game")
    proj.set_active(c, p.id)
    tsk.add(c, p.id, "Dash du joueur", tags=["gameplay"])
    tsk.add(c, p.id, "Fix du cooldown", description="dash related bug")
    tsk.add(c, p.id, "Menu pause")
    assert len(tsk.list(c, project_id=p.id, q="dash")) == 2  # title + description
    assert len(tsk.list(c, project_id=p.id, q="DASH")) == 2  # case-insensitive
    assert len(tsk.list(c, project_id=p.id, q="100%")) == 0   # LIKE wildcard escaped
    c.close()
    # through the API too
    api = Api(home=home)
    assert api.tasks({"q": "pause"})["count"] == 1
    assert api.tasks({"q": "pause", "priority": "medium"})["count"] == 1


def test_subtasks_via_api(api):
    parent = api.tasks_add({"title": "Combat"})
    sub = api.tasks_add({"title": "Hitbox", "parent_id": parent["id"]})
    assert sub["parent_id"] == parent["id"]
    listed = api.tasks({"all": "1"})["tasks"]
    assert sum(1 for t in listed if t["parent_id"] == parent["id"]) == 1


# --- devlog edit / delete -----------------------------------------------------

def test_logs_update_and_delete(api):
    e = api.logs_add({"content": "decision: buffer 0.15s", "tags": ["decision"]})
    r = api.logs_update({"id": e["id"], "content": "decision: buffer 0.2s"})
    assert r["content"] == "decision: buffer 0.2s"
    with pytest.raises(ApiError):
        api.logs_update({"id": e["id"], "content": "   "})  # empty refused
    with pytest.raises(ApiError) as e404:
        api.logs_delete({"id": 999})
    assert e404.value.status == 404
    d = api.logs_delete({"id": e["id"]})
    assert d["deleted"] == e["id"]
    assert api.logs_list({"limit": "50"})["count"] == 0


def test_core_logs_delete(home):
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    p = proj.create(c, "Game")
    e = logs.add(c, "note", project_id=p.id)
    logs.delete(c, e.id)
    assert logs.get(c, e.id) is None
    with pytest.raises(KeyError):
        logs.delete(c, e.id)
    c.close()


# --- feature detach -----------------------------------------------------------

def test_features_detach(api):
    t = api.tasks_add({"title": "Dash"})
    api.features_add({"name": "dash"})
    api.features_attach({"ref": "dash", "task_id": t["id"]})
    assert api.tasks({"all": "1"})["tasks"][0]["feature_id"] is not None
    r = api.features_detach({"ref": "dash", "task_id": t["id"]})
    assert r["progress"]["total"] == 0
    assert api.tasks({"all": "1"})["tasks"][0]["feature_id"] is None  # detach really clears it
    with pytest.raises(ApiError) as e:
        api.features_detach({"ref": "dash"})
    assert e.value.status == 400


# --- export / demo seed ---------------------------------------------------------

def test_export_now(api, home):
    api.tasks_add({"title": "X"})
    r = api.export_now({})
    p = Path(r["path"])
    assert p.exists() and str(home) in r["path"] and "exports" in r["path"]
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["tables"]["projects"] and payload["tables"]["tasks"]
    assert r["rows"] > 0


def test_demo_seed_guard_and_force(api):
    with pytest.raises(ApiError) as e:  # projects exist -> refuse without force
        api.demo_seed({})
    assert e.value.status == 400
    r = api.demo_seed({"force": True})
    assert r.get("projects") == 2  # demo creates Aether Vale + Portfolio Web
    assert api.projects()["projects"].__len__() >= 3


# --- HTTP end-to-end -------------------------------------------------------------

def _post(base, path, body):
    return urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json"}, method="POST")


def test_http_gap_endpoints(home):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        json.loads(urllib.request.urlopen(_post(base, "/api/projects", {"name": "G", "set_active": True})).read())
        t = json.loads(urllib.request.urlopen(_post(base, "/api/tasks/add", {"title": "Dash"})).read())
        # edit task over HTTP
        r = json.loads(urllib.request.urlopen(_post(base, "/api/tasks/update",
                                                    {"id": t["id"], "priority": "high", "tags": ["gameplay"]})).read())
        assert r["priority"] == "high" and r["tags"] == ["gameplay"]
        # search over HTTP
        res = json.loads(urllib.request.urlopen(base + "/api/tasks?q=dash&all=1").read())
        assert res["count"] == 1
        # log edit + delete over HTTP
        e = json.loads(urllib.request.urlopen(_post(base, "/api/logs/add", {"content": "note"})).read())
        json.loads(urllib.request.urlopen(_post(base, "/api/logs/update", {"id": e["id"], "content": "note v2"})).read())
        assert json.loads(urllib.request.urlopen(base + "/api/logs?limit=10").read())["entries"][0]["content"] == "note v2"
        d = json.loads(urllib.request.urlopen(_post(base, "/api/logs/delete", {"id": e["id"]})).read())
        assert d["deleted"] == e["id"]
        # demo seed over HTTP (empty-ish db has 1 project -> force)
        r = json.loads(urllib.request.urlopen(_post(base, "/api/demo/seed", {"force": True})).read())
        assert r.get("projects") == 2
        # export over HTTP
        x = json.loads(urllib.request.urlopen(_post(base, "/api/export", {})).read())
        assert Path(x["path"]).exists()
        # bad body -> 400 not 500
        try:
            urllib.request.urlopen(_post(base, "/api/tasks/update", {"id": "abc"}))
            assert False
        except urllib.error.HTTPError as err:
            assert err.code == 400
    finally:
        srv.shutdown()
        srv.server_close()
