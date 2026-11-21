"""Core devlog tests: add (default active project), search, list
filters, update, events.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devdash.core import db, events, logs, projects, tasks


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home: Path):
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def project(conn):
    p = projects.create(conn, "Game", engine="unity")
    projects.set_active(conn, p.id)
    return p


def test_log_add_defaults_to_active_project(conn, project):
    t = tasks.add(conn, project.id, "Dash")
    e1 = logs.add(conn, "Décision : dash = 0.3s d'i-frames", tags=["decision", "combat"], task_id=t.id)
    assert e1.project_id == project.id and e1.task_id == t.id
    assert e1.tags == ["decision", "combat"]
    e2 = logs.add(conn, "Bloqué sur le build WebGL", tags=["blocker"])
    assert e2.project_id == project.id and e2.task_id is None
    # newest first
    assert [e.id for e in logs.list(conn)] == [e2.id, e1.id]
    # events emitted
    assert any(e.action == "created" and e.entity == "log" for e in events.since(conn, 0))


def test_log_search_and_filters(conn, project):
    logs.add(conn, "Dash OK sur build local", tags=["combat"])
    logs.add(conn, "Idée : météo dynamique", tags=["idea"])
    logs.add(conn, "Build WebGL cassé", tags=["blocker"])
    assert [e.content for e in logs.search(conn, "dash")] == ["Dash OK sur build local"]
    # SQLite LIKE is case-insensitive for ASCII: "build" matches both entries
    assert len(logs.search(conn, "build")) == 2
    assert len(logs.list(conn, query="webgl")) == 1   # only "Build WebGL cassé"
    assert len(logs.list(conn, query="météo")) == 1


def test_log_validation(conn, project):
    with pytest.raises(ValueError):
        logs.add(conn, "   ")
    with pytest.raises(KeyError):
        logs.add(conn, "x", project_id=999)


def test_log_without_active_project(conn):
    with pytest.raises(KeyError):
        logs.add(conn, "x")


def test_log_update(conn, project):
    e = logs.add(conn, "brouillon")
    e2 = logs.update(conn, e.id, content="version corrigée", tags=["decision"])
    assert e2.content == "version corrigée" and e2.tags == ["decision"]
    with pytest.raises(KeyError):
        logs.update(conn, 999, content="x")
