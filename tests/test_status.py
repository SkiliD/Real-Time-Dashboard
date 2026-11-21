"""Stats + status snapshot tests (step g)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from devdash.core import db, projects, sessions, stats, status, tasks


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


def _backdate_session(conn, session_id: int, days_ago: int, minutes: float) -> None:
    start = datetime.now(timezone.utc) - timedelta(days=days_ago, minutes=minutes)
    end = start + timedelta(minutes=minutes)
    conn.execute(
        "UPDATE sessions SET started_at = ?, ended_at = ?, duration_min = ? WHERE id = ?",
        (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), minutes, session_id),
    )
    conn.commit()


def test_time_by_day_fills_gaps(conn, project):
    s1 = sessions.start(conn)
    _backdate_session(conn, s1.id, 0, 30)   # today
    s2 = sessions.start(conn)
    _backdate_session(conn, s2.id, 2, 60)   # 2 days ago
    days = stats.time_by_day(conn, project.id, days=5)
    assert len(days) == 5
    assert days[-1]["minutes"] == 30.0
    assert days[-3]["minutes"] == 60.0
    assert days[-2]["minutes"] == 0.0 and days[-4]["minutes"] == 0.0
    assert stats.today_minutes(conn, project.id) == 30.0


def test_streak_days(conn, project):
    for ago, mins in ((0, 10), (1, 20), (2, 5)):
        s = sessions.start(conn)
        _backdate_session(conn, s.id, ago, mins)
    assert stats.streak_days(conn, project.id) == 3
    # a gap: delete yesterday's session entirely
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    conn.execute("DELETE FROM sessions WHERE substr(started_at, 1, 10) = ?", (yesterday,))
    conn.commit()
    assert stats.streak_days(conn, project.id) == 1  # only today remains


def test_progress_weighted(conn, project):
    tasks.add(conn, project.id, "A", estimate_min=100)
    t2 = tasks.add(conn, project.id, "B", estimate_min=50)
    tasks.add(conn, project.id, "C")  # no estimate -> default 60
    tasks.done(conn, t2.id)
    prog = stats.project_progress(conn, project.id)
    assert prog["total"] == 3 and prog["done"] == 1
    assert prog["pct_tasks"] == 33.3
    assert prog["pct_weighted"] == round(100 * 50 / 210, 1)


def test_snapshot_full(conn, project):
    t1 = tasks.add(conn, project.id, "Dash", priority="high")
    tasks.start(conn, t1.id)
    tasks.add(conn, project.id, "Menu")
    s = sessions.start(conn, task_id=t1.id)
    # session still running, but started 5 minutes ago
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (past, s.id))
    conn.commit()
    snap = status.snapshot(conn)
    assert snap["project"]["slug"] == "game"
    assert snap["tasks"]["open"] == 2
    assert snap["tasks"]["in_progress"][0]["id"] == t1.id
    assert snap["session"]["id"] == s.id and snap["session"]["elapsed_min"] >= 0
    assert snap["today_minutes"] >= 5.0
    assert snap["streak_days"] == 0  # running session does not count yet
    assert snap["progress"]["total"] == 2
    assert snap["git"]["linked"] is False
    assert snap["last_event_id"] > 0
    # once stopped, the day counts toward the streak
    sessions.stop(conn, s.id, note="ok")
    snap2 = status.snapshot(conn)
    assert snap2["session"] is None
    assert snap2["streak_days"] == 1


def test_snapshot_no_active_project(conn):
    snap = status.snapshot(conn)
    assert snap["project"] is None and "tasks" not in snap
