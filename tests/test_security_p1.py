# tests -- priority-1 audit fixes (P1 batch + B1):
# S1 engine whitelist (stored-XSS sink), S2/S3 strict git arg filtering
# (--no-index, attached short opts, reflog writes, absolute paths),
# S4 security headers on every response, P1 wheel ships gui/static,
# B1 git-banner reads the {verdict: ...} envelope of /api/ai/task_verdict.
# Works on Windows, Mac and Linux.

from __future__ import annotations

import json
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, gitwatch, projects as proj
from devdash.gui.server import make_server

ROOT = Path(__file__).resolve().parent.parent


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


# --- S1: engine is a safe slug (it is rendered in the GUI) ----------------------

def test_engine_must_be_a_safe_slug(conn):
    p = proj.create(conn, "Ok", engine="Unity")          # normalized to lowercase
    assert p.engine == "unity"
    p = proj.create(conn, "Ok2", engine="custom-2")
    assert p.engine == "custom-2"
    for bad in ("<img src=x onerror=alert(1)>", "my engine", "a/b", "é", "x" * 41, 123):
        with pytest.raises(ValueError):
            proj.create(conn, "Bad", engine=bad)
    with pytest.raises(ValueError):
        proj.update(conn, p.id, engine="<script>alert(1)</script>")
    # update keeps working for a valid engine
    p = proj.update(conn, p.id, engine="GODOT")
    assert p.engine == "godot"


def test_gui_escapes_engine_label():
    js = (ROOT / "devdash" / "gui" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'escapeHtml(T("engine." + p.engine))' in js  # sidebar + header (defense in depth)


# --- S2/S3: git arguments are filtered fail-closed -------------------------------

REFUSED = [
    "diff --no-index /etc/passwd /etc/hostname",      # read outside the repo
    "diff --no-index C:\\Windows\\win.ini C:\\Windows\\system.ini",
    "grep --no-index secret -- /abs/path",
    "log --no-index",
    "branch -mfoo",                                     # attached short opt (rename = write)
    "tag -dfoo",                                        # attached short opt (delete = write)
    "reflog delete",                                    # write despite 'reflog show' being read-only
    "reflog expire",
    "ls-files /abs/path",                               # absolute path positional
    "log --git-dir=/tmp/evil",                          # long opt in --opt=value form
    "log --force-with-lease",                           # long prefix match
    "show HEAD --work-tree=/tmp",
]

ALLOWED = ["log --oneline -5", "branch -a", "status", "show HEAD", "diff --stat",
           "log -p -1", "tag -l", "rev-parse HEAD", "blame -- a.txt"]


@pytest.mark.parametrize("cmd", REFUSED)
def test_run_readonly_refuses_dangerous_args(git_repo, cmd):
    r = gitwatch.run_readonly(str(git_repo), cmd)
    assert r["ok"] is False, cmd
    assert "not allowed" in r["stderr"] or "only 'reflog show'" in r["stderr"], cmd


@pytest.mark.parametrize("cmd", ALLOWED)
def test_run_readonly_still_allows_readonly_forms(git_repo, cmd):
    r = gitwatch.run_readonly(str(git_repo), cmd)
    assert r["ok"] is True, (cmd, r["stderr"])


def test_run_remote_read_also_filters_absolute_paths(git_repo):
    r = gitwatch.run_remote_read(str(git_repo), "fetch /abs/path")
    assert r["ok"] is False and "not allowed" in r["stderr"]
    r = gitwatch.run_remote_read(str(git_repo), "remote -v")
    assert r["ok"] is True


# --- S4: security headers on every response ---------------------------------------

def test_security_headers_on_all_responses(home):
    srv = make_server(port=0, home=home)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        for path in ("/", "/static/app.js", "/api/status", "/api/nope"):  # 200 html/js/api + 404
            try:
                resp = urllib.request.urlopen(base + path)
            except urllib.error.HTTPError as e:  # 404 still carries the headers
                resp = e
            with resp:
                assert resp.headers.get("X-Frame-Options") == "DENY", path
                csp = resp.headers.get("Content-Security-Policy") or ""
                assert "frame-ancestors 'none'" in csp, path
                assert "default-src 'self'" in csp, path
                assert resp.headers.get("X-Content-Type-Options") == "nosniff", path
        with urllib.request.urlopen(base + "/") as resp:
            assert resp.headers.get("Server", "").startswith("DevDash/0.9"), resp.headers.get("Server")
    finally:
        srv.shutdown()
        srv.server_close()


# --- P1: the wheel must ship the GUI static files ---------------------------------

def test_wheel_package_data_includes_gui_static():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    gui_pd = data["tool"]["setuptools"]["package-data"]["devdash.gui"]
    assert "static/*" in gui_pd and "static/vendor/*" in gui_pd
    import importlib.resources
    assert importlib.resources.files("devdash.gui").joinpath("static/app.js").is_file()
    assert importlib.resources.files("devdash.gui").joinpath("static/vendor/chart.umd.min.js").is_file()


# --- B1: git banner reads the {verdict: ...} envelope ------------------------------

def test_git_banner_uses_verdict_envelope():
    js = (ROOT / "devdash" / "gui" / "static" / "app.js").read_text(encoding="utf-8")
    seg = js.find('api.post("/api/ai/task_verdict", { id: lt.id })')
    assert seg != -1, "git linked-task verdict call not found"
    window = js[seg:seg + 400]
    assert "r.verdict" in window, "banner must read r.verdict (API returns {verdict: {...}})"
