"""Adversarial security probes against a LIVE in-process HTTP server.

Hostile requests (forged Host/Origin, wrong Content-Type, huge bodies,
traversal paths, injection payloads) sent with http.client to a make_server
instance running on 127.0.0.1 in a thread — the way a drive-by page would try.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from devdash.core import config as cfgmod
from devdash.gui.server import make_server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def srv(home: Path):
    s = make_server(port=0, home=home)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def _port(srv) -> int:
    return srv.server_address[1]


def _conn(srv) -> http.client.HTTPConnection:
    return http.client.HTTPConnection("127.0.0.1", _port(srv), timeout=5)


def _req(srv, method: str, path: str, body=None, headers: dict | None = None):
    """Return (status, headers, body_bytes)."""
    c = _conn(srv)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        c.close()


def _get(srv, path: str):
    req = urllib.request.Request(f"http://127.0.0.1:{_port(srv)}" + path)
    return urllib.request.urlopen(req, timeout=5)


def _post(srv, path: str, body, content_type: str = "application/json"):
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    if isinstance(data, str):
        data = data.encode()
    return _req(srv, "POST", path, body=data, headers={"Content-Type": content_type})


# --- Host header (drive-by / DNS-rebinding protection) ------------------------------

def test_forged_host_rejected(srv):
    port = _port(srv)
    for bad in ("evil.example", "127.0.0.1.evil.com", "localhost:9999", "localhost.evil.com"):
        st, _h, _b = _req(srv, "GET", "/", headers={"Host": bad})
        assert st == 403, (bad, st)
    st, _h, body = _req(srv, "GET", "/", headers={"Host": f"127.0.0.1:{port}"})
    assert st == 200 and b"<html" in body.lower()


# --- POST hardening -------------------------------------------------------------------

def test_post_requires_json_content_type(srv):
    st, _h, _b = _post(srv, "/api/projects", {"name": "x"}, content_type="text/plain")
    assert st == 415
    st, _h, _b = _post(srv, "/api/projects", {"name": "x"},
                       content_type="application/x-www-form-urlencoded")
    assert st == 415
    st, _h, body = _post(srv, "/api/projects", {"name": "probe"})
    assert st == 200 and json.loads(body)["slug"] == "probe"


def test_post_missing_content_length_411(srv):
    c = _conn(srv)
    try:
        c.putrequest("POST", "/api/projects", skip_accept_encoding=True)
        c.putheader("Host", f"127.0.0.1:{_port(srv)}")
        c.putheader("Content-Type", "application/json")
        c.endheaders(b'{"name":"x"}')  # no Content-Length header at all
        r = c.getresponse()
        assert r.status == 411
        r.read()
    finally:
        c.close()


def test_post_oversized_body_413(srv):
    c = _conn(srv)
    try:
        c.putrequest("POST", "/api/tasks/add", skip_accept_encoding=True)
        c.putheader("Host", f"127.0.0.1:{_port(srv)}")
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", "2000000")  # lie: declare 2 MB, send nothing
        c.endheaders()
        r = c.getresponse()
        assert r.status == 413
        r.read()
    finally:
        c.close()


def test_post_invalid_content_length_400(srv):
    for cl in ("abc", "-5"):
        c = _conn(srv)
        try:
            c.putrequest("POST", "/api/projects", skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{_port(srv)}")
            c.putheader("Content-Type", "application/json")
            c.putheader("Content-Length", cl)
            c.endheaders()
            r = c.getresponse()
            assert r.status == 400, (cl, r.status)
            r.read()
        finally:
            c.close()


# --- static file traversal --------------------------------------------------------------

def test_static_path_traversal_blocked(srv):
    for path in ("/../devdash.db", "/..%2F..%2Fconfig.json", "/%2e%2e/%2e%2e/config.json",
                 "/..%5c..%5cconfig.json", "/api/../../devdash/core/db.py",
                 "/static/../../../devdash.db", "/./../README.md"):
        st, _h, _b = _req(srv, "GET", path)
        assert st in (400, 403, 404), (path, st)
    st, _h, body = _req(srv, "GET", "/")
    assert st == 200 and b"<html" in body.lower()
    st, h, _b = _req(srv, "GET", "/static/app.js")
    assert st == 200 and h.get("X-Content-Type-Options") == "nosniff"


# --- backup/export path confinement -------------------------------------------------------

def test_backup_out_path_confined(srv, home):
    st, _h, _b = _post(srv, "/api/backup", {"out": "C:/Windows/Temp/evil.zip"})
    assert st == 400
    st, _h, _b = _post(srv, "/api/backup", {"out": str(home.parent / "escape.zip")})
    assert st == 400
    st, _h, body = _post(srv, "/api/backup", {"out": str(home / "backups")})
    assert st == 200
    assert Path(json.loads(body)["path"]).is_file()


# --- secrets never leave the API -----------------------------------------------------------

def test_api_key_never_in_responses(srv, home):
    cfg = cfgmod.load_config(home)
    cfg.ai_enabled = True
    cfg.ai_provider = "openai_compat"
    cfg.ai_model = "test-model"
    cfg.ai_api_key = "sk-SECRET-9f8e7d6c"
    cfg.ai_base_url = "http://127.0.0.1:1/v1"  # refused connection -> error path
    cfgmod.save_config(cfg, home)
    secret = "sk-SECRET-9f8e7d6c"
    for path in ("/api/settings", "/api/ai/status", "/api/ai/config", "/api/ai/providers",
                 "/api/status", "/api/projects", "/api/events?since=0"):
        st, _h, body = _req(srv, "GET", path)
        assert st == 200 and secret not in body.decode(), path
    # an AI call that fails must not leak the key either
    st, _h, body = _post(srv, "/api/ai/ask", {"message": "hi"})
    assert secret not in body.decode()
    # and the key must not be persisted in the DB
    from devdash.core import db
    c = db.connect(home / "devdash.db")
    for table in ("events", "git_commits", "log_entries", "tasks"):
        rows = c.execute(f"SELECT * FROM {table}").fetchall()
        assert all(secret not in str(row) for row in rows)
    c.close()


# --- injection attempts ----------------------------------------------------------------------

def test_sql_injection_attempts_are_inert(srv):
    st, _h, _b = _post(srv, "/api/projects", {"name": "x'; DROP TABLE projects;--"})
    assert st == 200
    st, _h, body = _req(srv, "GET", "/api/projects")
    names = [p["name"] for p in json.loads(body)["projects"]]
    assert any("DROP TABLE" in n for n in names)  # stored as plain text, table intact
    st, _h, body = _req(srv, "GET", "/api/tasks?tag=%27%20OR%20%271%27%3D%271")
    data = json.loads(body)
    assert st == 200 and isinstance(data, (list, dict))


def test_xss_payload_is_json_not_html(srv):
    _post(srv, "/api/projects", {"name": "xssprobe"})
    st, _h, body = _post(srv, "/api/tasks/add", {"title": "<script>alert(1)</script>",
                                                 "project": "xssprobe"})
    assert st == 200 and "id" in json.loads(body)
    st, h, body = _req(srv, "GET", "/api/tasks")
    assert h.get("Content-Type", "").startswith("application/json")
    assert h.get("X-Content-Type-Options") == "nosniff"
    titles = [t["title"] for t in json.loads(body)["tasks"]]
    assert "<script>alert(1)</script>" in titles  # stored raw, rendered escaped client-side


# --- input bounds ------------------------------------------------------------------------------

def test_limits_are_clamped_not_infinite(srv):
    for path in ("/api/logs?limit=-50", "/api/logs?limit=99999999", "/api/git/commits?limit=-1"):
        st, _h, body = _req(srv, "GET", path)
        data = json.loads(body)
        assert st == 200 and isinstance(data, (list, dict)), path


def test_detect_path_is_bounded_metadata_only(srv, home):
    st, _h, _b = _req(srv, "GET", "/api/detect?path=" + "a" * 5000)
    assert st == 400
    st, _h, body = _req(srv, "GET", "/api/detect?path=" + str(home))
    assert st == 200
    data = json.loads(body)
    assert set(data) <= {"exists", "is_dir", "engine", "git_root", "name", "suggested_name", "ok", "path"}


# --- MCP server must survive garbage ------------------------------------------------------------

def test_mcp_survives_garbage_lines(home):
    env = dict(os.environ, DEVDASH_HOME=str(home), PYTHONPATH=str(ROOT))
    proc = subprocess.Popen(
        [sys.executable, "-m", "devdash", "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, env=env, cwd=ROOT,
    )
    try:
        garbage = '[1,2,3]\n"hello"\n42\nnull\n{"jsonrpc":"2.0"}\n'
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05",
                                      "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
        out, _ = proc.communicate(garbage + init + "\n", timeout=30)
        lines = [json.loads(l) for l in out.strip().splitlines() if l.strip()]
        # every garbage line got an error response, the valid one a result, server alive
        assert len(lines) == 5  # '{"jsonrpc":"2.0"}' has no method -> notification, no reply
        assert all("error" in l for l in lines[:-1])
        assert lines[-1].get("result", {}).get("serverInfo", {}).get("name") == "devdash"
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_updater_git_env_is_scrubbed():
    import os
    from devdash.core import updater
    os.environ["GIT_CONFIG_PARAMETERS"] = "evil"
    try:
        env = updater._git_env()
        assert "GIT_CONFIG_PARAMETERS" not in env
        assert env["GIT_TERMINAL_PROMPT"] == "0"
    finally:
        del os.environ["GIT_CONFIG_PARAMETERS"]


def test_mcp_tool_error_is_generic_for_unexpected_exceptions(monkeypatch):
    from devdash import mcp_server
    def boom(name, args, home):
        raise RuntimeError("secret path C:/Users/x/../../etc and SQL: SELECT * FROM t")
    monkeypatch.setattr(mcp_server, "_call", boom)
    resp = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "devdash_tasks", "arguments": {}}}, None)
    txt = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is True
    assert "internal error" in txt and "C:/Users" not in txt and "SELECT" not in txt


def test_redos_alternation_in_quantified_group_rejected():
    from devdash.core.config import _has_nested_quantifier
    assert _has_nested_quantifier("(a|a)+") is True
    assert _has_nested_quantifier("(a|a?)+") is True
    assert _has_nested_quantifier("(ab)+") is False
    assert _has_nested_quantifier("#(\d+)") is False
