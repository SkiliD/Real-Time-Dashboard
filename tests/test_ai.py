"""AI assistant tests — all with the deterministic "mock" provider,
no network. Verifies: disabled-by-default, plan propose-vs-apply (no write
without consent), verdict proposes but NEVER marks done, context hygiene.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devdash.core import config as cfgmod
from devdash.core import db, features, projects, tasks
from devdash.core.ai import ai_status, context, get_provider
from devdash.core.ai import assistant
from devdash.core.ai.provider import AIDisabledError


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


def enable_mock_ai(home: Path, **overrides) -> None:
    data = {"ai_enabled": True, "ai_provider": "mock", "ai_model": "mock"}
    data.update(overrides)
    (home / "config.json").write_text(json.dumps(data), encoding="utf-8")


def test_disabled_by_default(home, conn, project):
    cfg = cfgmod.load_config(home)
    assert get_provider(cfg) is None
    assert ai_status(cfg)["enabled"] is False
    with pytest.raises(AIDisabledError):
        assistant.ask(conn, "test", home=home)


def test_ask_and_summarize_mock(home, conn, project):
    enable_mock_ai(home)
    r = assistant.ask(conn, "où en suis-je ?", home=home)
    assert "mock" in r["answer"] and r["provider"] == "mock"
    s = assistant.summarize_week(conn, home=home)
    assert s["summary"]


def test_plan_propose_then_apply(home, conn, project):
    enable_mock_ai(home)
    # proposal only: NOTHING is written without consent
    r = assistant.plan(conn, "système de dash", apply=False, home=home)
    assert r["applied"] is False and len(r["proposal"]) == 3
    assert len(tasks.list(conn, project_id=project.id)) == 0
    # explicit consent: --apply creates the subtasks
    r2 = assistant.plan(conn, "système de dash", apply=True, home=home)
    assert r2["applied"] is True and len(r2["created_task_ids"]) == 3
    assert len(tasks.list(conn, project_id=project.id)) == 3


def test_verdict_proposes_never_done(home, conn, project):
    enable_mock_ai(home)
    f = features.create(conn, project.id, "dash")
    t = tasks.add(conn, project.id, "Dash movement")
    features.attach(conn, f.id, t.id)
    tasks.done(conn, t.id)
    r = assistant.feature_verdict(conn, "dash", home=home)
    assert r["verdict"]["seems_complete"] is True
    assert r["proposed"] is True
    f2 = features.get(conn, f.id)
    assert f2.status == "proposed"  # JAMAIS 'done' automatiquement
    assert f2.source == "ai" and f2.confidence == 0.8


def test_context_is_compact_and_bounded(home, conn, project):
    tasks.add(conn, project.id, "Une tâche")
    ctx = context.build_context(conn, max_chars=500)
    assert len(ctx) <= 500 + 30  # + truncation marker
    assert "Game" in ctx  # project name present


def test_feature_flag_off(home, conn, project):
    enable_mock_ai(home, ai_feature_flags={"chat": False, "summarize": True, "plan": True, "feature_verdict": True})
    with pytest.raises(AIDisabledError):
        assistant.ask(conn, "test", home=home)
    # but summarize still works
    assert assistant.summarize_week(conn, home=home)["summary"]
