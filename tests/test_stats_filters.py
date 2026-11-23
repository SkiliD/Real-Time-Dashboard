# tests -- stats filters
# Read-only on purpose.

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from devdash.core import db, projects as proj, sessions, stats
from devdash.gui.api import Api, ApiError
from devdash.gui.server import make_server


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home):
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    yield c
    c.close()


def _seed(conn):
    p1 = proj.create(conn, "Game", engine="unity")
    p2 = proj.create(conn, "Site", engine="web")
    proj.set_active(conn, p1.id)
    # sessions across several days (local dates)
    from devdash.core.models import local_now
    from datetime import timedelta
    for i, (pid, mins) in enumerate([(p1.id, 30), (p1.id, 45), (p2.id, 60), (p1.id, 15)]):
        s = sessions.start(conn, pid)
        # backdate: rewrite started_at/ended_at directly for deterministic days
        day = (local_now() - timedelta(days=i)).isoformat(timespec="seconds")
        conn.execute("UPDATE sessions SET started_at=?, ended_at=?, duration_min=? WHERE id=?",
                     (day, day, mins, s.id))
        conn.commit()
    return p1, p2


# --- core ---------------------------------------------------------------------

def test_regroup_rows_week_month(conn):
    rows = [{"date": "2026-11-17", "minutes": 30},   # Tuesday of ISO week 47
            {"date": "2026-11-18", "minutes": 45},   # same week
            {"date": "2026-11-23", "minutes": 15},   # next week (48)
            {"date": "2026-10-30", "minutes": 60}]   # previous month
    week = stats.regroup_rows(rows, "week", "minutes")
    assert [r["date"] for r in week] == ["2026-W44", "2026-W47", "2026-W48"]  # ISO week labels
    assert week[1]["minutes"] == 75  # 30 + 45 merged
    month = stats.regroup_rows(rows, "month", "minutes")
    assert [r["date"] for r in month] == ["2026-10", "2026-11"]
    assert month[1]["minutes"] == 90
    assert stats.regroup_rows(rows, "day", "minutes") == rows  # day = unchanged


def test_minutes_by_project(conn):
    p1, p2 = _seed(conn)
    bp = stats.minutes_by_project(conn, days=30)
    by_slug = {r["slug"]: r["minutes"] for r in bp}
    assert by_slug["game"] == 90 and by_slug["site"] == 60
    assert bp[0]["slug"] == "game"  # most active first
    assert "color" in bp[0]


# --- API -----------------------------------------------------------------------

def test_api_stats_filters(home, conn):
    p1, p2 = _seed(conn)
    api = Api(home=home)

    # default: active project, 14 days, grouped by day
    s = api.stats_overview({})
    assert s["project"] == p1.id and s["group"] == "day" and s["days"] == 14
    assert len(s["time_by_day"]) == 14
    assert any(r["minutes"] == 30 for r in s["time_by_day"])

    # week grouping
    s = api.stats_overview({"group": "week", "days": "30"})
    assert all("-W" in r["date"] for r in s["time_by_day"])
    assert len(s["time_by_day"]) <= 6

    # month grouping
    s = api.stats_overview({"group": "month", "days": "90"})
    assert all(len(r["date"]) == 7 for r in s["time_by_day"])  # YYYY-MM

    # explicit project by slug
    s = api.stats_overview({"project": "site"})
    assert s["project"] == p2.id
    assert sum(r["minutes"] for r in s["time_by_day"]) == 60

    # all projects aggregated
    s = api.stats_overview({"project": "all", "days": "30"})
    assert s["project"] is None
    assert sum(r["minutes"] for r in s["time_by_day"]) == 150
    assert {r["slug"] for r in s["by_project"]} == {"game", "site"}
    assert s["progress"] is None  # progress is per-project only

    # validation
    with pytest.raises(ApiError):
        api.stats_overview({"group": "year"})
    with pytest.raises(ApiError):
        api.stats_overview({"days": "9999"})


# --- HTTP -----------------------------------------------------------------------

def test_http_stats_filters(home, conn):
    _seed(conn)
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        s = json.loads(urllib.request.urlopen(base + "/api/stats?group=week&days=30&project=all").read())
        assert s["project"] is None and s["group"] == "week" and s["by_project"]
        s = json.loads(urllib.request.urlopen(base + "/api/stats?days=7").read())
        assert s["days"] == 7 and len(s["time_by_day"]) == 7
    finally:
        srv.shutdown()
        srv.server_close()
