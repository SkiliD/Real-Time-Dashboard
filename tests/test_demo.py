from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import db, demo, projects, stats

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def _conn(home: Path):
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    return c


def test_seed_core(home):
    conn = _conn(home)
    summary = demo.seed(conn)
    assert summary["projects"] == 2 and summary["tasks"] == 8
    active = projects.get_active(conn)
    assert active is not None and active.slug == "aether-vale"
    # stats see the backdated week
    assert stats.period_minutes(conn, active.id, 7) > 0
    assert stats.streak_days(conn, active.id) >= 1
    conn.close()


def test_cli_demo_seed_and_refusal(home):
    r = runner.invoke(app, ["--json", "demo", "seed"])
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)
    assert d["active_project"] == "aether-vale"
    # second run without --force is refused (no demo pollution)
    r = runner.invoke(app, ["demo", "seed"])
    assert r.exit_code != 0
    # --force works
    r = runner.invoke(app, ["--json", "demo", "seed", "--force"])
    assert r.exit_code == 0, r.output
