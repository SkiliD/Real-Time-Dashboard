"""GUI tests: the JSON Api directly + the HTTP server end-to-end.

pywebview itself (the native window) is not tested here; the server and API
are what matter, and both GUI modes share them.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from devdash.gui.api import Api
from devdash.gui.server import make_server


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def test_api_projects_and_tasks(home):
    api = Api(home=home)
    assert api.projects()["active"] is None

    p = api.projects_create({"name": "Aether Vale", "engine": "unity", "color": "#7c5cff",
                             "status": "prototype", "set_active": True})
    assert p["slug"] == "aether-vale"
    assert api.projects()["active"] == "aether-vale"

    t = api.tasks_add({"title": "Dash", "priority": "high", "tags": ["gameplay"], "estimate_min": 90})
    assert t["project"] == "aether-vale" and t["status"] == "todo"

    t2 = api.tasks_set_status({"id": t["id"], "status": "in_progress"})
    assert t2["status"] == "in_progress"

    listed = api.tasks({"project": "aether-vale", "status": "in_progress"})
    assert listed["count"] == 1

    t3 = api.tasks_set_status({"id": t["id"], "status": "done"})
    assert t3["completed_at"] is not None

    st = api.status()
    assert st["active"]["slug"] == "aether-vale"
    assert st["last_event_id"] > 0

    ev = api.events_since(0)
    assert ev["last_id"] >= 1 and len(ev["events"]) >= 3  # project.created, task.created, ...
    assert api.events_since(ev["last_id"])["events"] == []

    # switch project is lossless through the API too
    api.projects_create({"name": "Site", "engine": "web"})
    api.projects_set_active({"ref": "site"})
    assert api.projects()["active"] == "site"
    assert api.tasks({"project": "aether-vale"})["count"] == 1

    # tasks_add falls back to the active project (site) and succeeds
    t_site = api.tasks_add({"title": "Landing"})
    assert t_site["project"] == "site"
    # explicit bad ref -> ApiError
    with pytest.raises(Exception):
        api.projects_set_active({"ref": "nope"})


def test_http_server_end_to_end(home):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        # index.html served
        html = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "DevDash" in html and "/static/app.js" in html
        # static asset served
        js = urllib.request.urlopen(base + "/static/app.js").read().decode("utf-8")
        assert "api/events" in js
        # path traversal blocked
        try:
            urllib.request.urlopen(base + "/static/../api.py")
            assert False, "traversal should 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404

        # POST create project
        req = urllib.request.Request(
            base + "/api/projects",
            data=json.dumps({"name": "Game", "engine": "godot", "set_active": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        data = json.loads(urllib.request.urlopen(req).read())
        assert data["slug"] == "game"

        # POST add task (uses active project)
        req = urllib.request.Request(
            base + "/api/tasks/add",
            data=json.dumps({"title": "Jump", "priority": "critical", "tags": ["gameplay"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        t = json.loads(urllib.request.urlopen(req).read())
        assert t["title"] == "Jump" and t["project"] == "game"

        # GET tasks with filter
        tasks = json.loads(urllib.request.urlopen(base + "/api/tasks?priority=critical").read())
        assert tasks["count"] == 1

        # POST status + events endpoint
        req = urllib.request.Request(
            base + "/api/tasks/status",
            data=json.dumps({"id": t["id"], "status": "done"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        done = json.loads(urllib.request.urlopen(req).read())
        assert done["status"] == "done"

        ev = json.loads(urllib.request.urlopen(base + "/api/events?since=0").read())
        assert ev["last_id"] > 0 and any(e["entity"] == "task" for e in ev["events"])

        # error path: unknown project -> clean JSON 404
        req = urllib.request.Request(
            base + "/api/projects/set-active",
            data=json.dumps({"ref": "ghost"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404
            assert "ghost" in json.loads(e.read())["error"]

        # --- sessions ---
        req = urllib.request.Request(
            base + "/api/session/start",
            data=json.dumps({"kind": "focus", "minutes": 25}).encode(),
            headers={"Content-Type": "application/json"},
        )
        s = json.loads(urllib.request.urlopen(req).read())
        assert s["ended_at"] is None and s["planned_min"] == 25

        cur = json.loads(urllib.request.urlopen(base + "/api/session/current").read())
        assert cur["running"] is True and cur["session"]["id"] == s["id"]

        req = urllib.request.Request(
            base + "/api/session/stop",
            data=json.dumps({"note": "fait X"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        stopped = json.loads(urllib.request.urlopen(req).read())
        assert stopped["note"] == "fait X" and stopped["duration_min"] is not None

        hist = json.loads(urllib.request.urlopen(base + "/api/sessions?limit=10").read())
        assert hist["count"] == 1

        st = json.loads(urllib.request.urlopen(base + "/api/status").read())
        assert st["active"]["slug"] == "game" and st["session"] is None

        # --- devlog ---
        req = urllib.request.Request(
            base + "/api/logs/add",
            data=json.dumps({"content": "Décision : dash = 0.3s i-frames", "tags": ["decision"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        e = json.loads(urllib.request.urlopen(req).read())
        assert e["project"] == "game" and e["tags"] == ["decision"]
        urllib.request.urlopen(urllib.request.Request(
            base + "/api/logs/add",
            data=json.dumps({"content": "Bloqué sur le build WebGL"}).encode(),
            headers={"Content-Type": "application/json"},
        ))
        all_logs = json.loads(urllib.request.urlopen(base + "/api/logs").read())
        assert all_logs["count"] == 2
        found = json.loads(urllib.request.urlopen(base + "/api/logs?q=dash").read())
        assert found["count"] == 1 and "i-frames" in found["entries"][0]["content"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_git_endpoints(home, git_repo):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        req = urllib.request.Request(
            base + "/api/projects",
            data=json.dumps({"name": "Game", "engine": "unity", "set_active": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        json.loads(urllib.request.urlopen(req).read())

        req = urllib.request.Request(
            base + "/api/git/link",
            data=json.dumps({"path": str(git_repo)}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(req).read())["project"] == "game"

        req = urllib.request.Request(base + "/api/git/sync", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        synced = json.loads(urllib.request.urlopen(req).read())
        assert synced["repos"][0]["new"] == 3

        st = json.loads(urllib.request.urlopen(base + "/api/git/status").read())
        assert st["linked"] is True and st["commits_today"] == 3

        cs = json.loads(urllib.request.urlopen(base + "/api/git/commits?task=2").read())
        assert cs["count"] == 1 and cs["commits"][0]["parsed_task_id"] == 2

        cs = json.loads(urllib.request.urlopen(base + "/api/git/commits?unparsed=1").read())
        assert cs["count"] == 1
    finally:
        srv.shutdown()
        srv.server_close()


def test_http_features_endpoints(home):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())

    try:
        post("/api/projects", {"name": "Game", "set_active": True})
        post("/api/tasks/add", {"title": "Dash movement"})
        post("/api/tasks/add", {"title": "Dash i-frames"})

        f = post("/api/features/add", {"name": "dash"})
        assert f["status"] == "open"
        post("/api/features/attach", {"ref": "dash", "task_id": 1})
        post("/api/features/attach", {"ref": "dash", "task_id": 2})
        post("/api/tasks/status", {"id": 1, "status": "done"})
        post("/api/tasks/status", {"id": 2, "status": "done"})

        fl = json.loads(urllib.request.urlopen(base + "/api/features").read())
        assert fl["count"] == 1 and fl["features"][0]["progress"]["pct"] == 100.0
        assert fl["features"][0]["status"] == "open"  # never auto-done

        show = json.loads(urllib.request.urlopen(base + "/api/features/show?ref=dash").read())
        assert show["completion_signal"]["all_done"] is True and show["status"] == "open"

        # manual done via API = human decision
        done = post("/api/features/done", {"ref": "dash", "note": "OK"})
        assert done["status"] == "done" and done["validated_at"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_ai_endpoints(home):
    from devdash.gui.api import ApiError
    api = Api(home=home)
    # disabled by default -> clean 400
    st = api.ai_status()
    assert st["enabled"] is False
    with pytest.raises(ApiError):
        api.ai_ask({"question": "x"})

    # enable mock provider
    import json as _json
    (home / "config.json").write_text(_json.dumps(
        {"ai_enabled": True, "ai_provider": "mock", "ai_model": "mock"}), encoding="utf-8")
    st = api.ai_status()
    assert st["enabled"] is True and st["provider"] == "mock" and st["cloud"] is False

    api.projects_create({"name": "Game", "set_active": True})
    r = api.ai_ask({"question": "où en suis-je ?"})
    assert "answer" in r

    p = api.ai_plan({"title": "système de dash", "apply": False})
    assert p["applied"] is False and len(p["proposal"]) == 3
    p2 = api.ai_plan({"title": "système de dash", "apply": True})
    assert p2["applied"] is True and len(p2["created_task_ids"]) == 3
