"""Local HTTP server for the DevDash GUI (stdlib only).

Serves the static frontend (/) and a small JSON API (/api/*) backed by
gui.api.Api. The same server is embedded in the native desktop window
(PyWebView) or used standalone with `devdash gui --browser`.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from devdash.core import i18n

from .api import Api, ApiError

logger = logging.getLogger("devdash.gui")

STATIC_DIR = Path(__file__).parent / "static"

# Max accepted POST body size (JSON payloads are tiny; 1 MB is generous)
MAX_BODY_BYTES = 1_000_000

GET_ROUTES = {
    "/api/i18n": lambda api, q: {"lang": i18n.get_lang(), "messages": i18n.messages()},
    "/api/projects": lambda api, q: api.projects(),
    "/api/detect": lambda api, q: api.detect_path(q),
    "/api/tasks": lambda api, q: api.tasks(q),
    "/api/tags": lambda api, q: api.tags(q),
    "/api/status": lambda api, q: api.status(),
    "/api/events": lambda api, q: api.events_since(q.get("since", "0")),
    "/api/session/current": lambda api, q: api.session_current(),
    "/api/sessions": lambda api, q: api.sessions_list(q),
    "/api/logs": lambda api, q: api.logs_list(q),
    "/api/git/status": lambda api, q: api.git_status(q),
    "/api/git/commits": lambda api, q: api.git_commits(q),
    "/api/git/repos": lambda api, q: api.git_repos(),
    "/api/files": lambda api, q: api.files_list(),
    "/api/features": lambda api, q: api.features_list(q),
    "/api/features/show": lambda api, q: api.features_show(q),
    "/api/ai/status": lambda api, q: api.ai_status(),
    "/api/ai/context": lambda api, q: api.ai_context(),
    "/api/ai/providers": lambda api, q: api.ai_providers(),
    "/api/ai/config": lambda api, q: api.ai_config_get(),
    "/api/settings": lambda api, q: api.settings_get(),
    "/api/update/status": lambda api, q: api.update_status(),
    "/api/stats": lambda api, q: api.stats_overview(q),  # filters: ?project=all|<slug>&days=30&group=week
}

POST_ROUTES = {
    "/api/projects": lambda api, body: api.projects_create(body),
    "/api/projects/connect": lambda api, body: api.projects_connect(body),
    "/api/projects/set-active": lambda api, body: api.projects_set_active(body),
    "/api/projects/update": lambda api, body: api.projects_update(body),
    "/api/projects/delete": lambda api, body: api.projects_delete(body),
    "/api/tasks/add": lambda api, body: api.tasks_add(body),
    "/api/tasks/status": lambda api, body: api.tasks_set_status(body),
    "/api/tasks/update": lambda api, body: api.tasks_update(body),
    "/api/tasks/delete": lambda api, body: api.tasks_delete(body),
    "/api/session/start": lambda api, body: api.session_start(body),
    "/api/session/stop": lambda api, body: api.session_stop(body),
    "/api/logs/add": lambda api, body: api.logs_add(body),
    "/api/logs/update": lambda api, body: api.logs_update(body),
    "/api/logs/delete": lambda api, body: api.logs_delete(body),
    "/api/git/link": lambda api, body: api.git_link(body),
    "/api/git/unlink": lambda api, body: api.git_unlink(body),
    "/api/git/sync": lambda api, body: api.git_sync(body),
    "/api/git/ignore": lambda api, body: api.git_ignore(body),
    "/api/features/add": lambda api, body: api.features_add(body),
    "/api/features/attach": lambda api, body: api.features_attach(body),
    "/api/features/done": lambda api, body: api.features_done(body),
    "/api/features/propose": lambda api, body: api.features_propose(body),
    "/api/features/approve": lambda api, body: api.features_approve(body),
    "/api/features/reject": lambda api, body: api.features_reject(body),
    "/api/features/reopen": lambda api, body: api.features_reopen(body),
    "/api/features/detach": lambda api, body: api.features_detach(body),
    "/api/features/update": lambda api, body: api.features_update(body),
    "/api/features/delete": lambda api, body: api.features_delete(body),
    "/api/ai/ask": lambda api, body: api.ai_ask(body),
    "/api/ai/summarize": lambda api, body: api.ai_summarize(body),
    "/api/ai/plan": lambda api, body: api.ai_plan(body),
    "/api/ai/verdict": lambda api, body: api.ai_verdict(body),
    "/api/ai/task_verdict": lambda api, body: api.ai_task_verdict(body),
    "/api/ai/config": lambda api, body: api.ai_config_save(body),
    "/api/ai/test": lambda api, body: api.ai_config_test(body),
    "/api/settings": lambda api, body: api.settings_save(body),
    "/api/backup": lambda api, body: api.backup_now(body),
    "/api/export": lambda api, body: api.export_now(body),
    "/api/demo/seed": lambda api, body: api.demo_seed(body),
    "/api/update/check": lambda api, body: api.update_check(body),
    "/api/update": lambda api, body: api.update_now(body),
}


class Handler(BaseHTTPRequestHandler):
    api: Api
    static_dir: Path
    server_version = "DevDash/0.1"

    def log_message(self, *args):  # keep the console clean
        pass

    def _check_host(self) -> bool:
        """Anti-CSRF / anti-DNS-rebinding: the GUI is a local app, so only
        requests addressed to this exact local origin are accepted. A webpage
        opened in the user's browser cannot reach mutating routes with a
        forged Host (and a simple cross-site POST is rejected here)."""
        host = (self.headers.get("Host") or "").strip()
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if host not in allowed:
            self._send_json({"error": "forbidden host"}, 403)
            return False
        return True

    def _check_origin(self) -> bool:
        """Defense in depth on mutating requests: if the browser sends an
        Origin header (cross-site request), it must be our own local origin."""
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True  # same-origin / non-browser clients send no Origin
        port = self.server.server_address[1]
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}"}
        if origin not in allowed:
            self._send_json({"error": "forbidden origin"}, 403)
            return False
        return True

    # --- helpers ---
    def _send_json(self, obj, code: int = 200) -> None:
        payload = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path: Path) -> None:
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
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
        except Exception as e:
            # log full details server-side; the client gets a generic message
            # (a raw exception can leak absolute paths, SQL, etc.)
            logger.exception("API error on %s", self.path)
            self._send_json({"error": "internal server error"}, 500)

    # --- HTTP verbs ---
    def do_GET(self) -> None:
        if not self._check_host():
            return
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
        # is_relative_to (not startswith) so a sibling dir like "static-evil"
        # can never match; resolve() already blocks ".." escapes
        if not target.is_relative_to(self.static_dir.resolve()) or not target.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        self._send_file(target)

    def do_POST(self) -> None:
        if not self._check_host() or not self._check_origin():
            return
        parsed = urlparse(self.path)
        if parsed.path not in POST_ROUTES:
            self._send_json({"error": f"unknown route {parsed.path}"}, 404)
            return
        # API routes only accept real JSON: a cross-site "simple request"
        # (text/plain form post) must never reach a mutating route
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._send_json({"error": "Content-Type must be application/json"}, 415)
            return
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            self._send_json({"error": "Content-Length required"}, 411)
            return
        try:
            length = int(raw_len)
        except ValueError:
            self._send_json({"error": "invalid Content-Length"}, 400)
            return
        if length < 0:
            self._send_json({"error": "negative Content-Length"}, 400)
            return
        if length > MAX_BODY_BYTES:
            self._send_json({"error": "body too large"}, 413)
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        if not isinstance(body, dict):
            self._send_json({"error": "JSON body must be an object"}, 400)
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
