"""Tests for the P2/P3 batch: JS syntax check (node), GUI toast e2e over HTTP,
git re-sync on branch change (same HEAD), and `devdash export` JSON."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import config as cfgmod
from devdash.core import db, gitwatch, projects
from devdash.gui.server import make_server

runner = CliRunner()


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


# --- T3: the frontend JS must at least parse (node --check), when node exists ---

@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_app_js_parses():
    js = Path(__file__).parent.parent / "devdash" / "gui" / "static" / "app.js"
    proc = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_app_js_has_no_obvious_syntax_smoke():
    # node-free fallback: balanced braces/parens/brackets outside strings is
    # overkill; instead assert the file loads as text and key functions exist
    js = (Path(__file__).parent.parent / "devdash" / "gui" / "static" / "app.js").read_text(encoding="utf-8")
    for needle in ["function pollLoop", "POLL_HIDDEN_MS", "function showError", "async function runAction"]:
        assert needle in js


# --- T4: error toast is served + API errors are clean 400 JSON over HTTP --------

def test_http_serves_toast_and_400_json(home):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        html = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert 'id="toast"' in html  # the error container is in the served page
        # POST with missing title -> 400 with a JSON error body (not a 500)
        req = urllib.request.Request(
            base + "/api/tasks/add",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400
        body = json.loads(e.value.read())
        assert "title" in body["error"]
    finally:
        srv.shutdown(); srv.server_close()


# --- A3: branch change with identical HEAD still re-syncs ------------------------

def test_git_branch_change_triggers_resync(conn, project, git_repo):
    import subprocess as sp
    repo = gitwatch.link(conn, project.id, str(git_repo))
    pats = cfgmod.DEFAULT_COMMIT_PATTERNS
    r1 = gitwatch.sync_repo(conn, repo, pats)
    assert r1["new"] == 3 and not r1.get("skipped")
    r2 = gitwatch.sync_repo(conn, repo, pats)
    assert r2.get("skipped") is True  # same HEAD + same branch -> skipped
    # switch branch WITHOUT new commits: HEAD identical, branch differs
    sp.run(["git", "checkout", "-b", "feature-x"], cwd=repo, check=True, capture_output=True)
    r3 = gitwatch.sync_repo(conn, repo, pats)
    assert r3.get("skipped") is not True  # must NOT skip: branch changed
    st = gitwatch.status(conn, project.id)
    assert st["branch"] == "feature-x"


# --- A4: devdash export ------------------------------------------------------------

def test_export_command_json(home, conn, project, tmp_path):
    from devdash.core import tasks
    tasks.add(conn, project.id, "Export me", tags=["x"])
    out = tmp_path / "export.json"
    r = runner.invoke(app, ["--json", "export", "--out", str(out)])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["path"] == str(out) and data["rows"] > 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == db.SCHEMA_VERSION
    tables = payload["tables"]
    for t in ["projects", "tasks", "sessions", "log_entries", "features", "meta"]:
        assert t in tables
    assert any(p["slug"] == "game" for p in tables["projects"])
    assert any(t["title"] == "Export me" for t in tables["tasks"])
