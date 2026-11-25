# tests -- soft delete (corbeille) + restore for tasks / logs / features,
# via core, GUI API, CLI. Hard delete stays available with permanent=true.

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import config as cfgmod, db
from devdash.core import features as feats
from devdash.core import logs, projects, tasks
from devdash.gui.api import Api, ApiError

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home):
    c = db.connect(cfgmod.db_path(home))
    c.row_factory = sqlite3.Row
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def api(home) -> Api:
    a = Api(home=home)
    a.projects_create({"name": "Game", "engine": "unity", "set_active": True})
    return a


def test_schema_v9_adds_deleted_at():
    c = db.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.init_db(c)
    assert db.schema_version(c) == db.SCHEMA_VERSION == 11
    for t in ("tasks", "log_entries", "features"):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
        assert "deleted_at" in cols, t
    c.close()


def test_task_soft_delete_restore_cascade(conn):
    p = projects.create(conn, "P")
    t = tasks.add(conn, p.id, "parent")
    sub = tasks.add(conn, p.id, "child")
    tasks.update(conn, sub.id, parent_id=t.id)
    # soft delete: rows stay, flagged, hidden from list()
    tasks.delete(conn, t.id)
    assert tasks.get(conn, t.id).deleted_at is not None
    assert tasks.get(conn, sub.id).deleted_at is not None  # cascade to subtasks
    assert tasks.list(conn, project_id=p.id) == []
    assert len(tasks.list(conn, project_id=p.id, trash=True)) == 2
    # restore brings both back
    tasks.restore(conn, t.id)
    assert tasks.get(conn, t.id).deleted_at is None
    assert tasks.get(conn, sub.id).deleted_at is None
    assert len(tasks.list(conn, project_id=p.id)) == 2
    # hard delete still works and cascades via FK
    tasks.delete(conn, t.id, permanent=True)
    assert tasks.get(conn, t.id) is None and tasks.get(conn, sub.id) is None


def test_log_soft_delete_restore(conn):
    p = projects.create(conn, "P")
    e = logs.add(conn, "note", project_id=p.id)
    logs.delete(conn, e.id)
    assert logs.get(conn, e.id).deleted_at is not None
    assert logs.list(conn, project_id=p.id) == []
    assert len(logs.list(conn, project_id=p.id, trash=True)) == 1
    logs.restore(conn, e.id)
    assert logs.get(conn, e.id).deleted_at is None
    logs.delete(conn, e.id, permanent=True)
    assert logs.get(conn, e.id) is None


def test_feature_soft_delete_keeps_tasks_attached(conn):
    p = projects.create(conn, "P")
    f = feats.create(conn, p.id, "dash")
    t = tasks.add(conn, p.id, "implementer le dash", feature_id=f.id)
    feats.delete(conn, f.id, p.id)  # soft: tasks NOT detached
    assert feats.get(conn, f.id).deleted_at is not None
    assert tasks.get(conn, t.id).feature_id == f.id
    assert feats.list(conn, project_id=p.id) == []
    assert len(feats.list(conn, project_id=p.id, trash=True)) == 1
    feats.restore(conn, f.id, p.id)
    assert feats.get(conn, f.id).deleted_at is None
    # hard delete detaches tasks
    feats.delete(conn, f.id, p.id, permanent=True)
    assert feats.get(conn, f.id) is None
    assert tasks.get(conn, t.id).feature_id is None


def test_api_trash_flow(api):
    t = api.tasks_add({"title": "t1"})
    f = api.features_add({"name": "dash"})
    e = api.logs_add({"content": "note"})
    # soft delete by default
    assert api.tasks_delete({"id": t["id"]})["permanent"] is False
    assert api.features_delete({"ref": "dash"})["permanent"] is False
    assert api.logs_delete({"id": e["id"]})["permanent"] is False
    # hidden from the normal lists, visible in trash
    assert api.tasks({"all": "1"})["count"] == 0
    assert api.tasks({"all": "1", "deleted": "1"})["count"] == 1
    assert api.features_list({})["count"] == 0
    assert api.features_list({"deleted": "1"})["count"] == 1
    assert api.logs_list({})["count"] == 0
    assert api.logs_list({"deleted": "1"})["count"] == 1
    # restore
    assert api.tasks_restore({"id": t["id"]})["restored"] == t["id"]
    assert api.features_restore({"ref": "dash"})["restored"] == f["id"]
    assert api.logs_restore({"id": e["id"]})["restored"] == e["id"]
    assert api.tasks({"all": "1"})["count"] == 1
    # permanent delete from the trash
    api.tasks_delete({"id": t["id"], "permanent": True})
    assert api.tasks({"all": "1", "deleted": "1"})["count"] == 0
    with pytest.raises(ApiError):
        api.tasks_restore({"id": t["id"]})  # gone for good -> 404


def test_cli_delete_soft_then_restore(home):
    runner.invoke(app, ["--json", "projects", "create", "P", "--set-active"])
    tid = json.loads(runner.invoke(app, ["--json", "task", "add", "x"]).output)["id"]
    r = runner.invoke(app, ["--json", "task", "delete", str(tid), "--yes"])
    assert r.exit_code == 0 and json.loads(r.output)["soft"] is True
    assert json.loads(runner.invoke(app, ["--json", "task", "list"]).output)["count"] == 0
    # restore brings it back
    r = runner.invoke(app, ["--json", "task", "restore", str(tid)])
    assert r.exit_code == 0 and json.loads(r.output)["restored"] == tid
    assert json.loads(runner.invoke(app, ["--json", "task", "list"]).output)["count"] == 1
    # permanent delete emits no "soft" flag
    r = runner.invoke(app, ["--json", "task", "delete", str(tid), "--yes", "--permanent"])
    assert r.exit_code == 0 and "soft" not in json.loads(r.output)
    assert json.loads(runner.invoke(app, ["--json", "task", "list"]).output)["count"] == 0


def test_gui_trash_wiring():
    root = Path(__file__).resolve().parents[1]
    html = (root / "devdash/gui/static/index.html").read_text(encoding="utf-8")
    js = (root / "devdash/gui/static/app.js").read_text(encoding="utf-8")
    srv = (root / "devdash/gui/server.py").read_text(encoding="utf-8")
    for el in ('id="btn-trash"', 'id="trash-view"', 'id="log-trash"', 'id="feat-trash"'):
        assert el in html, el
    assert 'id="filter-trash"' not in html  # replaced by the 🗑 toggle button
    for route in ('"/api/tasks/restore"', '"/api/logs/restore"', '"/api/features/restore"'):
        assert route in srv, route
    for frag in ('q.set("deleted", "1")', '"/api/tasks/restore"', '"/api/logs/restore"',
                 '"/api/features/restore"', "permanent: true", "function renderTrashView"):
        assert frag in js, frag
