"""Local HTTP server for the DevDash GUI (stdlib only).

Serves the static frontend (/) and a small JSON API (/api/*) backed by
gui.api.Api. The same server is embedded in the native desktop window
(PyWebView) or used standalone with `devdash gui --browser`.
"""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from devdash.core import i18n

from .api import Api, ApiError

STATIC_DIR = Path(__file__).parent / "static"

GET_ROUTES = {
    "/api/i18n": lambda api, q: {"lang": i18n.get_lang(), "messages": i18n.messages()},
    "/api/projects": lambda api, q: api.projects(),
    "/api/tasks": lambda api, q: api.tasks(q),
    "/api/status": lambda api, q: api.status(),
    "/api/events": lambda api, q: api.events_since(q.get("since", "0")),
    "/api/session/current": lambda api, q: api.session_current(),
    "/api/sessions": lambda api, q: api.sessions_list(q),
    "/api/logs": lambda api, q: api.logs_list(q),
    "/api/git/status": lambda api, q: api.git_status(q),
    "/api/git/commits": lambda api, q: api.git_commits(q),
    "/api/features": lambda api, q: api.features_list(q),
    "/api/features/show": lambda api, q: api.features_show(q),
    "/api/ai/status": lambda api, q: api.ai_status(),
    "/api/stats": lambda api, q: api.stats_overview(q),
}

POST_ROUTES = {
    "/api/projects": lambda api, body: api.projects_create(body),
    "/api/projects/set-active": lambda api, body: api.projects_set_active(body),
    "/api/projects/update": lambda api, body: api.projects_update(body),
    "/api/tasks/add": lambda api, body: api.tasks_add(body),
    "/api/tasks/status": lambda api, body: api.tasks_set_status(body),
    "/api/session/start": lambda api, body: api.session_start(body),
    "/api/session/stop": lambda api, body: api.session_stop(body),
    "/api/logs/add": lambda api, body: api.logs_add(body),
    "/api/git/link": lambda api, body: api.git_link(body),
    "/api/git/unlink": lambda api, body: api.git_unlink(body),
    "/api/git/sync": lambda api, body: api.git_sync(body),
    "/api/features/add": lambda api, body: api.features_add(body),
    "/api/features/attach": lambda api, body: api.features_attach(body),
    "/api/features/done": lambda api, body: api.features_done(body),
    "/api/features/propose": lambda api, body: api.features_propose(body),
    "/api/features/approve": lambda api, body: api.features_approve(body),
    "/api/features/reject": lambda api, body: api.features_reject(body),
    "/api/features/reopen": lambda api, body: api.features_reopen(body),
    "/api/ai/ask": lambda api, body: api.ai_ask(body),
    "/api/ai/summarize": lambda api, body: api.ai_summarize(body),
    "/api/ai/plan": lambda api, body: api.ai_plan(body),
    "/api/ai/verdict": lambda api, body: api.ai_verdict(body),
}


class Handler(BaseHTTPRequestHandler):
    api: Api
    static_dir: Path
    server_version = "DevDash/0.1"

    def log_message(self, *args):  # keep the console clean
        pass

    # --- helpers ---
    def _send_json(self, obj, code: int = 200) -> None:
        payload = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path: Path) -> None:
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self, routes, arg) -> None:
        parsed = urlparse(self.path)
        fn = routes.get(parsed.path)
        if fn is None:
            self._send_json({"error": f"unknown route {parsed.path}"}, 404)
            return
        try:
            self._send_json(fn(self.api, arg))
        except ApiError as e:
            self._send_json({"error": str(e)}, e.status)
        except Exception as e:  # never leak a traceback to the client
            self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    # --- HTTP verbs ---
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in GET_ROUTES:
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            self._handle(GET_ROUTES, query)
            return
        # static files: "/" -> index.html, "/static/<f>" -> static_dir/<f>
        rel = parsed.path
        if rel == "/":
            rel = "/index.html"
        if not rel.startswith("/static/") and rel != "/index.html":
            self._send_json({"error": "not found"}, 404)
            return
        rel = rel[len("/static"):] if rel.startswith("/static/") else rel
        target = (self.static_dir / rel.lstrip("/")).resolve()
        if not str(target).startswith(str(self.static_dir.resolve())) or not target.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        self._send_file(target)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        self._handle(POST_ROUTES, body)


def make_server(port: int = 0, home: Path | None = None, static_dir: Path | None = None) -> ThreadingHTTPServer:
    """Build (but do not start) the GUI server. port=0 picks a free port."""
    api = Api(home=home)

    class _Handler(Handler):
        pass

    _Handler.api = api
    _Handler.static_dir = static_dir or STATIC_DIR
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.daemon_threads = True
    return server


def start_git_watcher(api: Api, interval_sec: int = 20) -> None:
    """Background thread: every interval, sync linked repos so new commits
    appear live in the GUI (each new commit emits a `git.commit` event)."""
    import threading
    import time

    def loop() -> None:
        while True:
            try:
                api.git_sync({})
            except Exception:
                pass  # watcher must never crash the GUI
            time.sleep(max(5, interval_sec))

    threading.Thread(target=loop, daemon=True, name="devdash-git-watcher").start()
