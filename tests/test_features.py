"""Feature tests (step h): grouping, progress, and the ABSOLUTE RULE —
a feature becomes 'done' only via an explicit human decision; check_completion
is a signal and never mutates status. Rejected proposals keep feedback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devdash.core import db, events, features, projects, tasks


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


def test_feature_lifecycle_manual_done(conn, project):
    f = features.create(conn, project.id, "dash", "Dash du joueur")
    t1 = tasks.add(conn, project.id, "Dash movement")
    t2 = tasks.add(conn, project.id, "Dash i-frames")
    features.attach(conn, f.id, t1.id)
    features.attach(conn, f.id, t2.id)

    prog = features.progress(conn, f.id)
    assert prog["total"] == 2 and prog["done"] == 0 and prog["pct"] == 0.0
    sig = features.check_completion(conn, f.id)
    assert sig["all_done"] is False
    assert features.get(conn, f.id).status == "open"  # signal did NOT change status

    tasks.done(conn, t1.id)
    assert features.check_completion(conn, f.id)["all_done"] is False
    assert features.get(conn, f.id).status == "open"  # still open: rule absolue

    tasks.done(conn, t2.id)
    sig = features.check_completion(conn, f.id)
    assert sig["all_done"] is True
    assert features.get(conn, f.id).status == "open"  # STILL open — never auto-done

    # human decision -> done
    f2 = features.approve(conn, f.id, note="testé en jeu")
    assert f2.status == "done" and f2.validated_at is not None
    evs = [e.action for e in events.since(conn, 0) if e.entity == "feature"]
    assert "validated" in evs
    # already done -> error
    with pytest.raises(ValueError):
        features.approve(conn, f.id)
    # reopen
    f3 = features.reopen(conn, f.id)
    assert f3.status == "open" and f3.validated_at is None


def test_propose_approve_reject_flow(conn, project):
    f = features.create(conn, project.id, "walljump")
    t = tasks.add(conn, project.id, "Walljump")
    features.attach(conn, f.id, t.id)
    tasks.done(conn, t.id)

    # AI-style proposal (step i will call this) — never done
    f = features.propose(conn, "walljump", confidence=0.85, reasoning="tâche faite + commits feat(walljump)")
    assert f.status == "proposed" and f.confidence == 0.85 and f.source == "ai"

    # human rejects -> feedback stored
    f = features.reject(conn, "walljump", feedback="pas encore testé en conditions réelles")
    assert f.status == "rejected" and "conditions réelles" in f.feedback

    # reopen then propose again then approve -> done by human
    features.reopen(conn, "walljump")
    features.propose(conn, "walljump", confidence=0.9, reasoning="re-test OK")
    f = features.approve(conn, "walljump")
    assert f.status == "done" and f.validated_at is not None


def test_feature_name_resolution_and_list(conn, project):
    features.create(conn, project.id, "dash")
    features.create(conn, project.id, "menu")
    rows = features.list(conn, project_id=project.id)
    assert {r["name"] for r in rows} == {"dash", "menu"}
    assert all("progress" in r for r in rows)
    # by name in active project
    assert features.get(conn, "dash").project_id == project.id
    with pytest.raises(KeyError):
        features.create(conn, 999, "x")
