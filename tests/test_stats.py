from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import db, projects, sessions, stats, tasks

runner = CliRunner()


@pytest.fixture(autouse=True)
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


def _session(conn, project_id, task_id, days_ago: int, minutes: float) -> None:
    s = sessions.start(conn, project_id=project_id, task_id=task_id)
    start = datetime.now(timezone.utc) - timedelta(days=days_ago, minutes=minutes)
    end = start + timedelta(minutes=minutes)
    conn.execute(
        "UPDATE sessions SET started_at=?, ended_at=?, duration_min=? WHERE id=?",
        (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), minutes, s.id),
    )
    conn.commit()


def test_by_tag_and_period(conn, project):
    t1 = tasks.add(conn, project.id, "Dash", tags=["gameplay", "combat"])
    t2 = tasks.add(conn, project.id, "Menu", tags=["ui"])
    _session(conn, project.id, t1.id, 0, 45)
    _session(conn, project.id, t1.id, 1, 30)
    _session(conn, project.id, t2.id, 3, 20)
    rows = {r["tag"]: r for r in stats.by_tag(conn, project.id)}
    assert rows["gameplay"]["tasks"] == 1 and rows["gameplay"]["minutes"] == 75.0
    assert rows["combat"]["minutes"] == 75.0
    assert rows["ui"]["tasks"] == 1 and rows["ui"]["minutes"] == 20.0
    assert stats.period_minutes(conn, project.id, 7) == 95.0
    assert stats.period_minutes(conn, project.id, 2) == 75.0


def test_cli_stats_json(home, conn, project):
    t = tasks.add(conn, project.id, "Dash", tags=["gameplay"])
    _session(conn, project.id, t.id, 0, 40)
    tasks.done(conn, t.id)

    r = runner.invoke(app, ["--json", "stats", "today"])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)
    assert d["today"] >= 40 and d["tasks_done_today"] == 1 and d["streak_days"] == 1

    r = runner.invoke(app, ["--json", "stats", "week"])
    d = json.loads(r.output)
    assert d["minutes"] >= 40 and len(d["per_day"]) == 7

    r = runner.invoke(app, ["--json", "stats", "streak"])
    assert json.loads(r.output)["streak_days"] == 1

    r = runner.invoke(app, ["--json", "stats", "tags"])
    assert json.loads(r.output)["by_tag"][0]["tag"] == "gameplay"


def test_api_stats_overview(home):
    from devdash.gui.api import Api
    api = Api(home=home)
    # no active project -> empty but valid
    empty = api.stats_overview({})
    assert empty["project"] is None and empty["minutes"]["today"] == 0

    api.projects_create({"name": "Game", "set_active": True})
    api.tasks_add({"title": "Dash", "tags": ["gameplay"]})
    api.session_start({"kind": "focus"})
    api.session_stop({"note": "x"})
    ov = api.stats_overview({"days": "14"})
    assert ov["project"] == 1
    assert len(ov["time_by_day"]) == 14
    assert ov["minutes"]["today"] >= 0
    assert ov["by_tag"][0]["tag"] == "gameplay"
    assert ov["progress"]["total"] == 1
