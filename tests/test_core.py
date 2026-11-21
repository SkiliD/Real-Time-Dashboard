"""Core tests: schema, projects (multi-project + lossless active
switch), tasks (filters, solo-focus start), events bus, config roundtrip.

Run with: python -m pytest -q
Each test gets an isolated DEVDASH_HOME (tmp_path) -- the real ~/.devdash is
never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devdash.core import config as cfgmod
from devdash.core import db, events, projects, tasks


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


# --- schema ---------------------------------------------------------------

def test_schema_created_and_stamped(conn):
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "meta", "projects", "features", "tasks", "sessions",
        "log_entries", "git_links", "git_commits", "git_sync_state", "events",
    } <= tables


# --- projects -------------------------------------------------------------

def test_project_create_unique_slugs(conn):
    p1 = projects.create(conn, "Aether Vale", engine="unity", color="#7c5cff")
    p2 = projects.create(conn, "Aether Vale", engine="web")  # same name -> slug-2
    assert p1.slug == "aether-vale"
    assert p2.slug == "aether-vale-2"
    assert len(projects.list(conn)) == 2


def test_active_switch_is_lossless(conn):
    """Switching the active project must not touch any other project's data."""
    p1 = projects.create(conn, "Game", engine="unity")
    p2 = projects.create(conn, "Website", engine="web")
    t1 = tasks.add(conn, p1.id, "Implement dash", tags=["gameplay"], priority="high")
    t2 = tasks.add(conn, p2.id, "SEO audit", tags=["seo"])

    projects.set_active(conn, p1.slug)
    assert projects.get_active(conn).id == p1.id

    projects.set_active(conn, "website")  # by slug
    assert projects.get_active(conn).id == p2.id

    # both projects and their tasks are fully intact
    assert tasks.get(conn, t1.id).title == "Implement dash"
    assert tasks.get(conn, t2.id).title == "SEO audit"
    assert {p.slug for p in projects.list(conn)} == {"game", "website"}


def test_project_update_and_archive(conn):
    p = projects.create(conn, "Game", engine="unity")
    projects.update(conn, p.id, status="development", color="#ff0000")
    p = projects.get(conn, p.id)
    assert p.status == "development" and p.color == "#ff0000"
    projects.update(conn, p.id, archived=True)
    assert projects.list(conn) == []                 # hidden by default
    assert len(projects.list(conn, include_archived=True)) == 1
    with pytest.raises(ValueError):
        projects.update(conn, p.id, status="nonsense")


# --- tasks ----------------------------------------------------------------

def test_task_lifecycle_filters_and_solo_focus(conn):
    p = projects.create(conn, "Game", engine="unity")
    t1 = tasks.add(conn, p.id, "Dash", priority="high", tags=["gameplay", "combat"], estimate_min=90)
    t2 = tasks.add(conn, p.id, "Menu UI", priority="low", tags=["ui"])
    t3 = tasks.add(conn, p.id, "Sound FX", priority="medium", tags=["audio"])
    sub = tasks.add(conn, p.id, "Dash i-frames", parent_id=t1.id, tags=["gameplay"])

    # filters
    assert [t.id for t in tasks.list(conn, project_id=p.id, tag="gameplay")] == [t1.id, sub.id]
    assert [t.id for t in tasks.list(conn, priority="high")] == [t1.id]
    # root filter returns all top-level tasks (order is status, then priority)
    assert sorted(t.id for t in tasks.list(conn, parent_id="root")) == [t1.id, t2.id, t3.id]
    # ordering: priority high before medium before low
    assert [t.id for t in tasks.list(conn, project_id=p.id, parent_id="root")] == [t1.id, t3.id, t2.id]
    assert tasks.list_tags(conn, project_id=p.id) == ["audio", "combat", "gameplay", "ui"]

    # start: solo focus -> only one in_progress per project
    tasks.start(conn, t1.id)
    tasks.start(conn, t2.id)
    assert tasks.get(conn, t1.id).status == "todo"        # bumped back by t2
    assert tasks.get(conn, t2.id).status == "in_progress"

    # done sets completed_at
    tasks.done(conn, t2.id)
    assert tasks.get(conn, t2.id).status == "done"
    assert tasks.get(conn, t2.id).completed_at is not None

    # validation errors
    with pytest.raises(ValueError):
        tasks.add(conn, p.id, "x", priority="urgent")
    with pytest.raises(KeyError):
        tasks.add(conn, 999, "x")


# --- events ---------------------------------------------------------------

def test_events_bus(conn):
    assert events.last_id(conn) == 0
    projects.create(conn, "Game")
    e1 = events.append(conn, "custom", None, "ping", {"n": 1})
    e2 = events.append(conn, "custom", 7, "pong")
    assert e2.id > e1.id
    got = events.since(conn, 0)
    assert [e.action for e in got] == ["created", "ping", "pong"]
    assert events.since(conn, e2.id) == []
    assert got[1].payload == {"n": 1}


# --- config / paths -------------------------------------------------------

def test_config_roundtrip_and_home_override(home, conn):  # conn fixture creates devdash.db
    assert cfgmod.devdash_home() == home                      # DEVDASH_HOME wins
    assert cfgmod.db_path() == home / "devdash.db"
    cfg = cfgmod.load_config()
    assert cfg.pomodoro_focus_min == 25 and cfg.pomodoro_break_min == 5
    assert cfg.ai_enabled is False                             # AI off by default
    cfg.ai_enabled = True
    cfg.ai_api_key = "secret-test-key"
    path = cfgmod.save_config(cfg)
    assert path == home / "config.json"
    reloaded = cfgmod.load_config()
    assert reloaded.ai_enabled is True and reloaded.ai_api_key == "secret-test-key"
    # config stays OUT of the database file (separation of data and secrets)
    raw_db = (home / "devdash.db").read_bytes()
    assert b"secret-test-key" not in raw_db
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "#(\\d+)" in data["commit_patterns"][0]
