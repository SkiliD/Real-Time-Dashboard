# tests -- persisted AI chat history (schema v8, /api/ai/chat, /api/ai/chat/clear)

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, projects
from devdash.core.ai import chat as ai_chat
from devdash.gui.api import Api


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home):
    c = db.connect(cfgmod.db_path(home))
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def project(conn):
    p = projects.create(conn, "Game", engine="unity")
    projects.set_active(conn, p.id)
    return p


def _enable_mock_ai(home: Path) -> None:
    (home / "config.json").write_text(
        json.dumps({"ai_enabled": True, "ai_provider": "mock", "ai_model": "mock"}),
        encoding="utf-8")


def test_schema_v8_creates_ai_messages(conn):
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ai_messages" in tables


def test_chat_append_history_clear_and_prune(conn, project):
    ai_chat.append(conn, project.id, "user", "q1")
    ai_chat.append(conn, project.id, "assistant", "a1", meta="mock / mock")
    hist = ai_chat.history(conn, project.id)
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[1]["content"] == "a1" and hist[1]["meta"] == "mock / mock"
    # chronological order, capped
    for i in range(5):
        ai_chat.append(conn, project.id, "user", f"q{i}")
    assert len(ai_chat.history(conn, project.id)) == 7
    assert len(ai_chat.history(conn, project.id, limit=3)) == 3
    # prune: keep only the newest MAX_HISTORY rows
    for i in range(ai_chat.MAX_HISTORY + 10):
        ai_chat.append(conn, project.id, "user", f"spam{i}")
    rows = conn.execute("SELECT COUNT(*) AS c FROM ai_messages WHERE project_id = ?",
                        (project.id,)).fetchone()["c"]
    assert rows == ai_chat.MAX_HISTORY
    # clear
    assert ai_chat.clear(conn, project.id) == ai_chat.MAX_HISTORY
    assert ai_chat.history(conn, project.id) == []
    with pytest.raises(ValueError):
        ai_chat.append(conn, project.id, "system", "nope")


def test_api_chat_history_persisted_by_ask_and_clearable(home, project):
    _enable_mock_ai(home)
    api = Api(home=home)
    assert api.ai_chat({})["messages"] == []
    r = api.ai_ask({"question": "où en suis-je ?"})
    assert r["provider"] == "mock"
    msgs = api.ai_chat({})["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "où en suis-je ?" and "mock" in msgs[1]["content"]
    assert "mock" in (msgs[1]["meta"] or "")
    # a second exchange appends, history survives a NEW Api instance (persisted)
    api.ai_ask({"question": "et demain ?"})
    msgs2 = Api(home=home).ai_chat({})["messages"]
    assert len(msgs2) == 4 and msgs2[-1]["role"] == "assistant"
    # clear
    assert api.ai_chat_clear({})["deleted"] == 4
    assert api.ai_chat({})["messages"] == []


def test_gui_chat_history_wiring():
    """The chat tab must load the persisted history on tab switch and offer a
    two-step clear button wired to /api/ai/chat/clear."""
    root = Path(__file__).resolve().parents[1]
    html = (root / "devdash/gui/static/index.html").read_text(encoding="utf-8")
    js = (root / "devdash/gui/static/app.js").read_text(encoding="utf-8")
    assert 'id="btn-chat-clear"' in html
    assert "async function loadAiChat" in js and 'api.get("/api/ai/chat")' in js
    assert "api.post(\"/api/ai/chat/clear\"" in js
    assert '$("#btn-chat-clear").onclick' in js
    # loaded on tab switch, not on every poll (would clobber in-flight messages)
    sw = js[js.index("function switchTab"):]
    sw = sw[:sw.index("\nfunction ", 10)]
    assert 'tab === "ai"' in sw and "loadAiChat" in sw
    assert "loadAiChat" not in js[js.index("async function realLoadAll"):js.index("async function loadTasks")]
