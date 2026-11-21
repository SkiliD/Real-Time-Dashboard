"""Tests for the last batch: MCP server (real stdio JSON-RPC subprocess),
`devdash import`, and GUI browser-level tests (Playwright when available,
plus an always-on HTTP flow that mimics a browser session)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import db, projects, tasks

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent


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


# --- MCP server: real subprocess, newline-delimited JSON-RPC over stdio --------

class McpClient:
    def __init__(self, home: Path):
        env = dict(os.environ, DEVDASH_HOME=str(home), PYTHONPATH=str(ROOT))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "devdash", "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, cwd=ROOT,
        )
        self._id = 0

    def call(self, method: str, params: dict | None = None, expect_response: bool = True):
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        if not expect_response:
            return None
        line = self.proc.stdout.readline()
        assert line, "MCP server closed stdout unexpectedly"
        return json.loads(line)

    def notify(self, method: str):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def test_mcp_initialize_tools_and_call(home, conn):
    projects.create(conn, "Aether Vale", engine="unity")
    projects.set_active(conn, 1)
    tasks.add(conn, 1, "Dash movement", priority="high", tags=["gameplay"])

    mcp = McpClient(home)
    try:
        r = mcp.call("initialize", {"protocolVersion": "2024-11-05",
                                    "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        assert r["result"]["serverInfo"]["name"] == "devdash"
        assert r["result"]["capabilities"]["tools"]

        mcp.notify("notifications/initialized")  # must NOT produce a response line

        r = mcp.call("tools/list")
        names = [t["name"] for t in r["result"]["tools"]]
        assert "devdash_status" in names and "devdash_task_add" in names and len(names) >= 10
        for t in r["result"]["tools"]:
            assert t["inputSchema"]["type"] == "object"

        r = mcp.call("tools/call", {"name": "devdash_projects_list", "arguments": {}})
        payload = json.loads(r["result"]["content"][0]["text"])
        assert r["result"]["isError"] is False
        assert payload["active"] == "aether-vale"
        assert payload["projects"][0]["engine"] == "unity"

        r = mcp.call("tools/call", {"name": "devdash_tasks_list", "arguments": {"tag": "gameplay"}})
        payload = json.loads(r["result"]["content"][0]["text"])
        assert payload["count"] == 1 and payload["tasks"][0]["title"] == "Dash movement"

        r = mcp.call("tools/call", {"name": "devdash_task_add", "arguments": {"title": "Wall jump"}})
        payload = json.loads(r["result"]["content"][0]["text"])
        assert r["result"]["isError"] is False and payload["title"] == "Wall jump"

        r = mcp.call("tools/call", {"name": "devdash_no_such_tool", "arguments": {}})
        assert r["result"]["isError"] is True

        r = mcp.call("bogus/method")
        assert r["error"]["code"] == -32601

        r = mcp.call("ping")
        assert r["result"] == {}
    finally:
        mcp.close()


# --- devdash import --------------------------------------------------------------

def test_export_then_import_roundtrip(home, conn, tmp_path):
    p = projects.create(conn, "Game", engine="godot")
    projects.set_active(conn, p.id)
    tasks.add(conn, p.id, "Imported task", tags=["io"])
    out = tmp_path / "export.json"
    r = runner.invoke(app, ["--json", "export", "--out", str(out)])
    assert r.exit_code == 0, r.output

    # refuse to merge into a non-empty DB without --force
    r = runner.invoke(app, ["import", str(out)])
    assert r.exit_code != 0 and "force" in r.output.lower() or "--force" in r.output

    # wipe and import into the now-empty DB (same home): simulate a fresh machine
    # by using a second home
    home2 = tmp_path / "home2"
    home2.mkdir()
    os.environ["DEVDASH_HOME"] = str(home2)
    try:
        r = runner.invoke(app, ["--json", "import", str(out)])
        assert r.exit_code == 0, r.output
        c2 = db.connect(home2 / "devdash.db")
        db.init_db(c2)
        projs = projects.list(c2)
        assert len(projs) == 1 and projs[0].slug == "game"
        ts = tasks.list(c2, project_id=projs[0].id)
        assert len(ts) == 1 and ts[0].title == "Imported task" and ts[0].tags == ["io"]
        c2.close()
    finally:
        os.environ["DEVDASH_HOME"] = str(home)


def test_import_rejects_bad_columns_and_files(home, tmp_path):
    from devdash.core.models import now_iso
    bad = tmp_path / "bad.json"
    row = {"slug": "x", "name": "x", "created_at": now_iso(), "updated_at": now_iso(),
           "evil_col; DROP TABLE projects;--": "y"}  # hostile column must be ignored
    bad.write_text(json.dumps({"tables": {"projects": [row]}}), encoding="utf-8")
    r = runner.invoke(app, ["--json", "import", str(bad)])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["rows"] == 1 and data["errors"] == 0
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    assert projects.list(c)[0].slug == "x"
    # table still exists (injection ignored)
    assert c.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    c.close()
    r = runner.invoke(app, ["import", str(tmp_path / "missing.json")])
    assert r.exit_code != 0


# --- GUI browser-level tests ------------------------------------------------------

def test_http_browser_like_full_flow(home):
    """Always-on: mimics a browser session over HTTP (page + assets + API flow)."""
    from devdash.gui.server import make_server
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def get(p):
        return urllib.request.urlopen(base + p).read()

    def post(p, body):
        req = urllib.request.Request(base + p, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return json.loads(urllib.request.urlopen(req).read())

    try:
        assert b"DevDash" in get("/")
        assert b"pollLoop" in get("/static/app.js")
        assert b"--red" in get("/static/styles.css")
        assert "messages" in json.loads(get("/api/i18n"))

        p = post("/api/projects", {"name": "Browser Game", "engine": "unity", "set_active": True})
        assert p["slug"] == "browser-game"
        t = post("/api/tasks/add", {"title": "UI task", "priority": "high", "tags": ["ui"]})
        post("/api/session/start", {"task_id": t["id"], "kind": "focus"})
        post("/api/session/stop", {"note": "done via http"})
        post("/api/logs/add", {"content": "note de test navigateur"})
        post("/api/features/add", {"name": "ui-flow"})
        post("/api/features/attach", {"ref": "ui-flow", "task_id": t["id"]})

        tasks_list = json.loads(get("/api/tasks?all=1"))["tasks"]
        assert any(x["title"] == "UI task" for x in tasks_list)
        sessions = json.loads(get("/api/sessions"))["sessions"]
        assert sessions and sessions[0]["note"] == "done via http"
        ev = json.loads(get("/api/events?since=0"))
        assert ev["last_id"] > 0 and len(ev["events"]) >= 5
        st = json.loads(get("/api/stats"))
        assert st["project"] == p["id"]
        feats = json.loads(get("/api/features"))["features"]
        assert feats[0]["progress"]["total"] == 1
    finally:
        srv.shutdown(); srv.server_close()


def test_gui_playwright_smoke(home, tmp_path):
    """Real browser test (runs where playwright+chromium are installed:
    `pip install playwright && playwright install chromium`)."""
    pytest.importorskip("playwright", reason="playwright not installed (pip install playwright && playwright install chromium)")
    from playwright.sync_api import sync_playwright
    from devdash.gui.server import make_server

    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector("#project-list")
            # create a project through the UI
            page.click("#btn-new-project")
            page.fill("#f-name", "E2E Game")
            page.click("#btn-create-project")
            page.wait_for_selector("#project-list li")
            assert "E2E Game" in page.inner_text("#project-list")
            # add a task through the UI -> kanban card appears
            page.fill("#new-task-title", "E2E task")
            page.click("#btn-add-task")
            page.wait_for_selector("#col-todo .card")
            assert "E2E task" in page.inner_text("#col-todo")
            # no error toast visible on the happy path
            assert page.is_hidden("#toast")
            browser.close()
    finally:
        srv.shutdown(); srv.server_close()
