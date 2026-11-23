# tests -- sessions

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from devdash.core import db, events, projects, sessions


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


def _age(conn, session_id: int, minutes: float) -> None:
    """Fake a session started `minutes` ago (no sleeping in tests)."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (past, session_id))
    conn.commit()


def test_planned_min_column_migrated(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    assert "planned_min" in cols
    assert db.schema_version(conn) == db.SCHEMA_VERSION


def test_start_stop_autostop_current(conn, project):
    from devdash.core import tasks
    t = tasks.add(conn, project.id, "Dash")
    s1 = sessions.start(conn, kind="focus", minutes=25)
    assert s1.planned_min == 25
    assert sessions.current(conn).id == s1.id

    s2 = sessions.start(conn, task_id=t.id, kind="custom")  # auto-stops s1
    assert sessions.get(conn, s1.id).ended_at is not None
    assert sessions.current(conn).id == s2.id
    assert s2.task_id == t.id

    _age(conn, s2.id, 5)
    s2 = sessions.stop(conn, note="fait le dash")
    assert s2.duration_min == 5.0
    assert s2.note == "fait le dash"
    assert sessions.current(conn) is None

    ev_actions = [e.action for e in events.since(conn, 0)]
    assert "started" in ev_actions and "stopped" in ev_actions


def test_stop_errors(conn, project):
    with pytest.raises(KeyError):
        sessions.stop(conn)  # nothing running
    s = sessions.start(conn)
    sessions.stop(conn, s.id)
    with pytest.raises(ValueError):
        sessions.stop(conn, s.id)  # already stopped


def test_start_without_active_project(conn):
    with pytest.raises(KeyError):
        sessions.start(conn)


def test_list_filters_and_totals(conn, project):
    s1 = sessions.start(conn)
    _age(conn, s1.id, 30)
    sessions.stop(conn, s1.id, note="a")
    s2 = sessions.start(conn)
    _age(conn, s2.id, 10)
    sessions.stop(conn, s2.id, note="b")
    # an old session from "last week"
    s3 = sessions.start(conn)
    _age(conn, s3.id, 60 * 24 * 10)
    sessions.stop(conn, s3.id, note="old")

    assert len(sessions.list(conn, today=True)) == 2      # s3 is 10 days old
    assert len(sessions.list(conn, week=True)) == 2
    assert len(sessions.list(conn)) == 3
    assert sessions.list(conn)[0].id == s2.id              # newest first (s3 is 10 days old)
    assert sessions.total_min(conn, today=True) == 40.0
    assert sessions.total_min(conn) == 40.0 + 60 * 24 * 10


def test_elapsed_running_session(conn, project):
    s = sessions.start(conn)
    _age(conn, s.id, 2.5)
    live = sessions.get(conn, s.id)
    assert abs(sessions.elapsed_min(live) - 2.5) < 0.05
