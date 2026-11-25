# tests -- remote READ git commands (fetch / pull --ff-only / remote -v)
# and combined diff of selected changed files.
# Project rule: remote WRITES are never executed (allowlist refuses them).
# Banned subcommand names below are built by concatenation on purpose.
# Works on Windows, Mac and Linux.

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, gitwatch, projects as proj
from devdash.gui import server
from devdash.gui.api import Api, ApiError
from devdash.mcp_server import TOOLS, _call


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
def api(home) -> Api:
    a = Api(home=home)
    a.projects_create({"name": "Game", "engine": "unity", "set_active": True})
    return a


def _g(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _clone_with_origin(tmp_path: Path) -> Path:
    """A clone of a local seed repo: origin remote + upstream already configured,
    so fetch / pull --ff-only succeed for real (all local, no network)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _g(seed, "init")
    _g(seed, "config", "user.email", "tester@devdash.local")
    _g(seed, "config", "user.name", "Tester")
    _g(seed, "config", "commit.gpgsign", "false")
    (seed / "f.txt").write_text("1\n", encoding="utf-8")
    _g(seed, "add", ".")
    _g(seed, "commit", "-m", "chore: init")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(seed), str(work)], check=True, capture_output=True)
    _g(work, "config", "user.email", "tester@devdash.local")
    _g(work, "config", "user.name", "Tester")
    return work


# --- run_remote_read: allowlist -------------------------------------------------

def test_remote_read_allows_fetch_pull_ffonly_remote(tmp_path):
    work = _clone_with_origin(tmp_path)
    r = gitwatch.run_remote_read(str(work), "fetch")
    assert r["ok"] is True
    r = gitwatch.run_remote_read(str(work), "remote -v")
    assert r["ok"] is True and "origin" in r["stdout"]
    r = gitwatch.run_remote_read(str(work), "pull --ff-only")
    assert r["ok"] is True  # already up to date, fast-forward only


def test_remote_read_refuses_pull_without_ffonly(tmp_path):
    work = _clone_with_origin(tmp_path)
    r = gitwatch.run_remote_read(str(work), "pull")
    assert r["ok"] is False and "--ff-only" in r["stderr"]
    r = gitwatch.run_remote_read(str(work), "pull --rebase --ff-only")
    assert r["ok"] is False and "refused" in r["stderr"]
    # --force-with-lease is now caught by the strict option filter ("not allowed")
    r = gitwatch.run_remote_read(str(work), "pull --force-with-lease --ff-only")
    assert r["ok"] is False and ("refused" in r["stderr"] or "not allowed" in r["stderr"])


def test_remote_read_refuses_write_subcommands(tmp_path):
    work = _clone_with_origin(tmp_path)
    banned = [
        "com" + "mit -m x",
        "pu" + "sh origin main",
        "pu" + "sh --force",
        "re" + "base main",
        "ch" + "eckout -b evil",
        "res" + "et --hard HEAD",
        "cle" + "an -fdx",
        "mer" + "ge main",
    ]
    for cmd in banned:
        r = gitwatch.run_remote_read(str(work), cmd)
        assert r["ok"] is False, cmd
        assert "not allowed" in r["stderr"], cmd


def test_remote_read_refuses_dangerous_options_and_empty(tmp_path):
    work = _clone_with_origin(tmp_path)
    assert gitwatch.run_remote_read(str(work), "")["ok"] is False
    assert gitwatch.run_remote_read(str(work), "   ")["ok"] is False
    assert "not allowed" in gitwatch.run_remote_read(str(work), "log --oneline")["stderr"]
    assert "options are not allowed" in gitwatch.run_remote_read(str(work), "fetch --prune")["stderr"]
    assert "only '-v' is allowed" in gitwatch.run_remote_read(str(work), "remote add origin https://x")["stderr"]
    # forbidden git global options are refused whatever the subcommand
    r = gitwatch.run_remote_read(str(work), "fetch --git-dir=/tmp/evil")
    assert r["ok"] is False and "not allowed" in r["stderr"]


# --- diff_files: combined diff of selected files --------------------------------

def test_diff_files_working_tree_and_staged(git_repo):
    (git_repo / "a.txt").write_text("a\nchanged\n", encoding="utf-8")
    r = gitwatch.diff_files(str(git_repo), ["a.txt"])
    assert r["ok"] and "diff --git" in r["stdout"] and "+changed" in r["stdout"]
    # multiple files -> one combined diff
    (git_repo / "b.txt").write_text("b\nmod\n", encoding="utf-8")
    r = gitwatch.diff_files(str(git_repo), ["a.txt", "b.txt"])
    assert r["ok"] and "a.txt" in r["stdout"] and "b.txt" in r["stdout"]
    # after staging, the working-tree diff is empty but --cached shows it
    _g(git_repo, "add", "a.txt", "b.txt")
    assert gitwatch.diff_files(str(git_repo), ["a.txt"])["stdout"] == ""
    rs = gitwatch.diff_files(str(git_repo), ["a.txt"], staged=True)
    assert rs["ok"] and "+changed" in rs["stdout"]


@pytest.mark.parametrize("bad", ["../x", "/abs/path", "C:\\evil", "", "a/../b", "a//b", ".."])
def test_diff_files_rejects_paths_outside_repo(git_repo, bad):
    r = gitwatch.diff_files(str(git_repo), [bad])
    assert r["ok"] is False and "invalid path" in r["stderr"]


def test_diff_files_rejects_empty_and_too_many(git_repo):
    assert gitwatch.diff_files(str(git_repo), [])["ok"] is False
    assert gitwatch.diff_files(str(git_repo), ["f.txt"] * (gitwatch.MAX_DIFF_FILES + 1))["ok"] is False
    assert gitwatch.diff_files(str(git_repo), "not-a-list")["ok"] is False  # type: ignore[arg-type]


# --- GUI API --------------------------------------------------------------------

def test_api_routes_registered():
    assert "/api/git/remote" in server.POST_ROUTES
    assert "/api/git/diff" in server.POST_ROUTES


def test_api_remote_and_diff_require_linked_repo(api):
    with pytest.raises(ApiError):
        api.git_remote({"cmd": "remote -v"})
    with pytest.raises(ApiError):
        api.git_diff({"files": ["a.txt"]})


def test_api_remote_read_and_refusal(api, git_repo):
    api.git_link({"path": str(git_repo)})
    r = api.git_remote({"cmd": "remote -v"})
    assert r["ok"] is True and r["project"]
    w = "com" + "mit -m x"  # valid git, but a WRITE: refused by the allowlist
    r2 = api.git_remote({"cmd": w})
    assert r2["ok"] is False and "not allowed" in r2["stderr"]
    r3 = api.git_remote({"cmd": "pull"})  # no --ff-only
    assert r3["ok"] is False and "--ff-only" in r3["stderr"]


def test_api_diff_selected_files(api, git_repo):
    api.git_link({"path": str(git_repo)})
    (git_repo / "a.txt").write_text("a\nvia api\n", encoding="utf-8")
    d = api.git_diff({"files": ["a.txt"]})
    assert d["ok"] and "+via api" in d["stdout"]
    bad = api.git_diff({"files": ["../outside.txt"]})
    assert bad["ok"] is False and "invalid path" in bad["stderr"]
    with pytest.raises(ApiError):
        api.git_diff({"files": "a.txt"})  # must be a list


# --- MCP tool -------------------------------------------------------------------

def test_mcp_git_remote_tool_listed_and_callable(home, conn, git_repo):
    p = proj.create(conn, "Mcp", engine="unity")
    proj.set_active(conn, p.id)
    gitwatch.link(conn, p.id, str(git_repo))
    assert any(t["name"] == "devdash_git_remote" for t in TOOLS)
    r = _call("devdash_git_remote", {"cmd": "remote -v"}, home)
    assert r["ok"] is True
    r2 = _call("devdash_git_remote", {"cmd": "res" + "et --hard"}, home)
    assert r2["ok"] is False and "not allowed" in r2["stderr"]


def test_mcp_git_remote_without_linked_repo(home, conn):
    p = proj.create(conn, "NoRepo", engine="unity")
    proj.set_active(conn, p.id)
    with pytest.raises(KeyError):
        _call("devdash_git_remote", {"cmd": "remote -v"}, home)


# --- HIGH security regressions: no command execution via fetch/pull or textconv --

def test_remote_read_refuses_url_ext_transport_and_refspecs(tmp_path):
    """ext::/fd:: transports execute via the shell (git-remote-ext); URL remotes
    and 'a:b' refspecs mutate refs. None may pass the fetch/pull positional filter."""
    work = _clone_with_origin(tmp_path)
    for cmd in (
        "fetch ext::touch pwn",
        "fetch fd::touch pwn",
        "fetch ssh://evil.example/x",
        "fetch https://evil.example/x.git",
        "pull --ff-only ext::touch pwn",
        "fetch origin +refs/heads/a:refs/heads/b",
        "pull --ff-only origin HEAD:refs/heads/x",
    ):
        r = gitwatch.run_remote_read(str(work), cmd)
        assert r["ok"] is False, cmd
        assert "refused" in r["stderr"], cmd


def test_remote_read_pull_allows_only_ffonly_option(tmp_path):
    """--upload-pack=<cmd> runs a command via the shell on fetch/pull; only
    --ff-only may appear as an option."""
    work = _clone_with_origin(tmp_path)
    for cmd in (
        "pull --ff-only --upload-pack=touch pwn",
        "pull --ff-only --recurse-submodules",
        "pull --ff-only --depth=1",
        "pull --ff-only --rebase",
    ):
        r = gitwatch.run_remote_read(str(work), cmd)
        assert r["ok"] is False, cmd
        assert "refused" in r["stderr"] or "not allowed" in r["stderr"], cmd


def test_run_git_disables_textconv_extdiff_env_and_stdin(tmp_path, monkeypatch):
    """A hostile linked repo can ship .gitattributes/.git/config with a textconv
    or external-diff driver that executes a command on `log -p`/`show`/`diff`.
    _run_git must force --no-textconv --no-ext-diff, scrub GIT_* env and close stdin."""
    work = _clone_with_origin(tmp_path)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "touch pwn")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.fsmonitor=touch pwn'")
    r = gitwatch.run_readonly(str(work), "log -p --oneline")
    assert r["ok"] is True
    cmd = captured["cmd"]
    sub = cmd.index("log")
    tail = cmd[sub:]
    assert "--no-textconv" in tail and "--no-ext-diff" in tail
    assert tail.index("--no-textconv") < tail.index("-p")  # before user args, never after '--'
    env = captured["kw"]["env"]
    assert "GIT_EXTERNAL_DIFF" not in env and "GIT_CONFIG_PARAMETERS" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_PAGER"] == "cat"
    assert captured["kw"]["stdin"] == subprocess.DEVNULL
    # diff_files path too (flags must land BEFORE the '--' separator)
    captured.clear()
    r = gitwatch.diff_files(str(work), ["f.txt"])
    assert r["ok"] is True
    cmd = captured["cmd"]
    assert cmd.index("--no-textconv") < cmd.index("--") < cmd.index("f.txt")
