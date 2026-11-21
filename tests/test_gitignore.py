"""`devdash git ignore` / POST /api/git/ignore — the marker-delimited DevDash
block in the USER's repo .gitignore: idempotent, preserves existing content,
adds the config.json line when DEVDASH_HOME lives inside the repo.
"""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import db, gitwatch, projects as proj
from devdash.gui.api import Api, ApiError
from devdash.gui.server import make_server

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "game"
    r.mkdir()

    def g(*a):
        subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t.local")
    g("config", "user.name", "T")
    g("config", "commit.gpgsign", "false")
    (r / "Assets").mkdir()
    (r / "Assets" / "a.txt").write_text("a")
    g("add", ".")
    g("commit", "-q", "-m", "init")
    return r


# --- core ---------------------------------------------------------------------

def test_ensure_gitignore_creates_and_is_idempotent(repo, home):
    r1 = gitwatch.ensure_gitignore(repo, home=home)
    assert r1["updated"] is True and r1["lines"] > 0
    text = (repo / ".gitignore").read_text(encoding="utf-8")
    assert gitwatch.IGNORE_BEGIN in text and gitwatch.IGNORE_END in text
    assert ".devdash/" in text and "devdash.db" in text and "devdash-backup-*.zip" in text
    # second run: no change
    r2 = gitwatch.ensure_gitignore(repo, home=home)
    assert r2["updated"] is False and r2["already_present"] is True
    assert (repo / ".gitignore").read_text(encoding="utf-8") == text


def test_ensure_gitignore_preserves_existing_content(repo, home):
    gi = repo / ".gitignore"
    gi.write_text("# my project\n*.log\nBuild/\n", encoding="utf-8")
    gitwatch.ensure_gitignore(repo, home=home)
    text = gi.read_text(encoding="utf-8")
    assert text.startswith("# my project\n*.log\nBuild/\n")
    assert text.count(gitwatch.IGNORE_BEGIN) == 1  # exactly one block
    # stale block replaced, not duplicated
    gitwatch.ensure_gitignore(repo, home=home)
    text2 = gi.read_text(encoding="utf-8")
    assert text2.count(gitwatch.IGNORE_BEGIN) == 1 and text2 == text


def test_ensure_gitignore_adds_config_json_when_home_inside_repo(repo):
    inside = repo / ".devdash"
    inside.mkdir()
    r = gitwatch.ensure_gitignore(repo, home=inside)
    text = (repo / ".gitignore").read_text(encoding="utf-8")
    assert ".devdash/config.json" in text  # API keys never committable


def test_ensure_gitignore_refuses_non_repo(tmp_path):
    with pytest.raises(gitwatch.GitError):
        gitwatch.ensure_gitignore(tmp_path)


# --- CLI ------------------------------------------------------------------------

def test_cli_git_ignore(home, repo):
    conn = db.connect(home / "devdash.db")
    db.init_db(conn)
    p = proj.create(conn, "Game", engine="unity")
    proj.set_active(conn, p.id)
    gitwatch.link(conn, p.id, str(repo))
    conn.close()

    r = runner.invoke(app, ["--json", "git", "ignore"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["updated"] is True and data["project"] == "game"
    assert gitwatch.IGNORE_BEGIN in (repo / ".gitignore").read_text(encoding="utf-8")
    # again -> already present
    r = runner.invoke(app, ["--json", "git", "ignore"])
    assert json.loads(r.output)["updated"] is False
    # no linked repo -> clean error
    conn = db.connect(home / "devdash.db")
    p2 = proj.create(conn, "NoGit")
    proj.set_active(conn, p2.id)
    conn.close()
    r = runner.invoke(app, ["git", "ignore"])
    assert r.exit_code != 0


# --- API / HTTP -----------------------------------------------------------------

def test_api_and_http_git_ignore(home, repo):
    api = Api(home=home)
    api.projects_create({"name": "Game", "set_active": True})
    api.git_link({"path": str(repo)})

    r = api.git_ignore({})
    assert r["updated"] is True and r["project"] == "game"
    r2 = api.git_ignore({})
    assert r2["updated"] is False
    # no link -> 400
    api.projects_create({"name": "Solo"})
    api.projects_set_active({"ref": "solo"})
    with pytest.raises(ApiError) as e:
        api.git_ignore({})
    assert e.value.status == 400

    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        api.projects_set_active({"ref": "game"})
        req = urllib.request.Request(base + "/api/git/ignore", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        d = json.loads(urllib.request.urlopen(req).read())
        assert d["updated"] is False  # already ensured above
    finally:
        srv.shutdown()
        srv.server_close()
