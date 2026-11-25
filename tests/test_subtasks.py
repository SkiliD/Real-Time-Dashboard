# tests -- subtask stacking (Trello/Notion-style parent tickets)
# Works on Windows, Mac and Linux.

from __future__ import annotations

from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, projects as proj, tasks
from devdash.gui.api import Api, ApiError
from devdash.gui import server


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


# --- core: descendants / stats / tree ----------------------------------------

def test_descendants_and_stats(conn):
    p = proj.create(conn, "Game")
    root = tasks.add(conn, p.id, "Epic")
    a = tasks.add(conn, p.id, "A", parent_id=root.id)
    b = tasks.add(conn, p.id, "B", parent_id=root.id)
    c = tasks.add(conn, p.id, "C", parent_id=a.id)  # grandchild
    tasks.add(conn, p.id, "other root")

    assert sorted(tasks.descendants(conn, root.id)) == sorted([a.id, b.id, c.id])
    assert tasks.descendants(conn, b.id) == []
    assert tasks.descendants(conn, 9999) == []  # unknown id: empty, no error

    tasks.done(conn, a.id)
    tasks.done(conn, c.id)
    st = tasks.subtask_stats(conn, root.id)
    assert st == {"total": 3, "done": 2, "pct": 67}
    assert tasks.subtask_stats(conn, b.id) == {"total": 0, "done": 0, "pct": 0}


def test_tree_nesting_progress_and_orphan_promotion(conn):
    p = proj.create(conn, "Game")
    root = tasks.add(conn, p.id, "Epic", priority="high")
    a = tasks.add(conn, p.id, "A", parent_id=root.id)
    c = tasks.add(conn, p.id, "C", parent_id=a.id)
    tasks.add(conn, p.id, "Solo")
    tasks.done(conn, c.id)

    tree = tasks.tree(conn, project_id=p.id)
    assert [n["title"] for n in tree] == ["Epic", "Solo"]  # priority high first
    epic = tree[0]
    assert [k["title"] for k in epic["children"]] == ["A"]
    assert epic["children"][0]["children"][0]["id"] == c.id
    assert epic["progress"] == {"total": 2, "done": 1, "pct": 50}
    assert tree[1]["children"] == [] and tree[1]["progress"]["total"] == 0

    # status filter: parent filtered out -> child promoted to root, not lost
    tree_done = tasks.tree(conn, project_id=p.id, status="done")
    assert [n["id"] for n in tree_done] == [c.id]  # only the done task, as a root


def test_parent_cycle_and_cross_project_rejected(conn):
    p = proj.create(conn, "Game")
    other = proj.create(conn, "Other")
    root = tasks.add(conn, p.id, "Epic")
    a = tasks.add(conn, p.id, "A", parent_id=root.id)
    with pytest.raises(ValueError):
        tasks.update(conn, root.id, parent_id=a.id)  # would create a cycle
    with pytest.raises(ValueError):
        tasks.update(conn, a.id, parent_id=a.id)  # self-parent
    foreign = tasks.add(conn, other.id, "F")
    with pytest.raises(ValueError):
        tasks.update(conn, a.id, parent_id=foreign.id)  # cross-project
    with pytest.raises(KeyError):
        tasks.update(conn, a.id, parent_id=424242)
    # unparent works (explicit None clears)
    tasks.update(conn, a.id, parent_id=None)
    assert tasks.get(conn, a.id).parent_id is None


# --- API: /api/tasks/tree + parenting through the API -------------------------

def test_api_tasks_tree_route_and_shape(api):
    assert "/api/tasks/tree" in server.GET_ROUTES
    root = api.tasks_add({"title": "Epic"})
    sub = api.tasks_add({"title": "Sub", "parent_id": root["id"]})
    api.tasks_set_status({"id": sub["id"], "status": "done"})
    r = api.tasks_tree({"all": "1"})
    assert r["count"] == 1
    node = r["tree"][0]
    assert node["id"] == root["id"] and node["progress"] == {"total": 1, "done": 1, "pct": 100}
    assert node["children"][0]["id"] == sub["id"]


def test_api_parent_cycle_is_a_clean_400(api):
    root = api.tasks_add({"title": "Epic"})
    sub = api.tasks_add({"title": "Sub", "parent_id": root["id"]})
    with pytest.raises(ApiError) as e:
        api.tasks_update({"id": root["id"], "parent_id": sub["id"]})
    assert e.value.status == 400 and "cycle" in str(e.value)
    with pytest.raises(ApiError) as e2:
        api.tasks_add({"title": "Bad", "parent_id": 9999})
    assert e2.value.status == 400
    # detach via explicit null
    r = api.tasks_update({"id": sub["id"], "parent_id": None})
    assert r["parent_id"] is None
