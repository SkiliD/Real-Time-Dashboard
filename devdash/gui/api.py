# REST endpoints consumed by app.js. Api is a thin facade composed of
# per-domain mixins (gui/api_<domain>.py); shared validation/connection
# helpers live here, module-level helpers in gui/api_common.py.
from __future__ import annotations

# NOTE: os/shutil/subprocess stay imported here even when unused locally —
# tests monkeypatch them through this module's namespace.
from .api_common import ApiError, _folder_picker_command, _general_block
from contextlib import contextmanager
from devdash.core import config
from devdash.core.models import MAX_TEXT_LEN
from devdash.core import db
from devdash.core import events as ev
from devdash.core import features as feats
from devdash.core import gitwatch
from devdash.core import projects as proj
from devdash.core import sessions as sess
from devdash.core import tasks as tsk
from pathlib import Path
import os
import shutil
import sqlite3
import subprocess
import threading

from .api_projects import ProjectsMixin
from .api_tasks import TasksMixin
from .api_features import FeaturesMixin
from .api_git import GitMixin
from .api_files import FilesMixin
from .api_logs import LogsMixin
from .api_sessions import SessionsMixin
from .api_stats import StatsMixin
from .api_ai import AiMixin
from .api_settings import SettingsMixin
from .api_system import SystemMixin
from .api_misc import MiscMixin


__all__ = ["Api", "ApiError", "_folder_picker_command", "_general_block"]


class Api(ProjectsMixin, TasksMixin, FeaturesMixin, GitMixin, FilesMixin, LogsMixin, SessionsMixin, StatsMixin, AiMixin, SettingsMixin, SystemMixin, MiscMixin):
    def __init__(self, home: Path | None = None):
        self.home = home  # None -> resolved from DEVDASH_HOME at call time
        self._db_ready = False
        self._db_lock = threading.Lock()
        self._local = threading.local()  # one pooled connection PER THREAD


    @contextmanager

    def _conn(self):
        """One pooled sqlite connection per thread (a connection is bound to its
        creating thread). The first call per PROCESS bootstraps the file (full
        setup: WAL + chmod + schema + event prune); pooled connections then use
        the light connect (no chmod syscall / no WAL pragma per request — WAL is
        persistent in the DB header). On exception the transaction is rolled
        back, which is what closing the connection used to guarantee."""
        home = self.home or config.devdash_home()
        if not self._db_ready:
            with self._db_lock:
                if not self._db_ready:
                    bootstrap = db.connect(config.db_path(home))  # full setup
                    try:
                        # init_db runs the full schema script + a write commit: ONCE
                        # per process, not on every request (polled 1.5s apart)
                        db.init_db(bootstrap)
                        ev.prune(bootstrap)  # bound the append-only event bus once per process
                    finally:
                        bootstrap.close()
                    self._db_ready = True
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = db.connect(config.db_path(home), setup=False)  # light hot path
            self._local.conn = conn
        try:
            yield conn
        except BaseException:
            try:
                conn.rollback()  # same guarantee the old close()-on-exit gave
            except sqlite3.Error:
                pass
            raise

    # --- input helpers: bad client input is a 400, never a 500 -------------

    # list endpoints never return more than this many rows (SQLite LIMIT -1
    # would mean "unlimited" — a client must not be able to ask for that)
    MAX_LIST_LIMIT = 1000


    @staticmethod

    def _int(value, what: str, default=None, hi: int | None = None):
        if value is None or value == "":
            return default
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ApiError(f"invalid {what}: {value!r} (expected an integer)")
        if hi is not None:
            n = max(1, min(hi, n))
        return n


    @staticmethod

    def _str_list(body: dict, key: str) -> list[str] | None:
        """Fetch an optional list[str] field; wrong type is a clean 400."""
        if key not in body or body[key] is None:
            return None
        v = body[key]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ApiError(f"invalid {key}: expected a list of strings")
        return v


    @staticmethod

    def _str(body: dict, key: str, default: str = "", required: bool = False, max_len: int = MAX_TEXT_LEN) -> str:
        """Fetch a string field; wrong type is a clean 400, never a 500."""
        if key not in body or body[key] is None:
            if required:
                raise ApiError(f"{key} is required")
            return default
        v = body[key]
        if not isinstance(v, str):
            raise ApiError(f"invalid {key}: expected a string, got {type(v).__name__}")
        if len(v) > max_len:
            raise ApiError(f"{key} is too long (max {max_len} chars)")
        return v


    @staticmethod

    def _tags(value) -> list[str]:
        """Validate a tags field: must be a list of strings (a bare string is NOT
        split into characters — that was a bug)."""
        if value is None:
            return []
        if isinstance(value, str) or not isinstance(value, list) \
                or not all(isinstance(t, str) for t in value):
            raise ApiError("tags must be a list of strings")
        return value


    def _list_pid(self, c, query: dict) -> int | None:
        """Scope for list endpoints: explicit project=... wins; 'all'/'*' means
        every project; otherwise the ACTIVE project (the GUI header shows the
        active project, so lists must match it — not silently mix all projects)."""
        ref = query.get("project")
        if ref in (None, ""):
            active = proj.get_active(c)
            return active.id if active else None
        if ref in ("all", "*"):
            return None
        return self._resolve_project(c, ref).id

    # --- projects ---------------------------------------------------------


    def _resolve_project(self, c, ref):
        if ref:
            p = proj.get(c, int(ref) if str(ref).isdigit() else str(ref))
            if p is None:
                raise ApiError(f"no project matching {ref!r}", status=404)
            return p
        p = proj.get_active(c)
        if p is None:
            raise ApiError("no active project (create one or pass project=...)", status=400)
        return p


    def _confined_out(self, body: dict | None, default_subdir: str):
        """Resolve the optional `out` path from a POST body. The file may only
        be written INSIDE DEVDASH_HOME — this endpoint is a local write
        primitive and must not become an arbitrary-path write."""
        home = Path(self.home or config.devdash_home()).resolve()
        raw = (body or {}).get("out")
        if not raw:
            return home / default_subdir
        out = Path(raw).expanduser().resolve()
        if not out.is_relative_to(home):
            raise ApiError(f"out must be inside DEVDASH_HOME ({home})", status=400)
        return out


    def _ai_call(self, fn, *args, **kwargs):
        from devdash.core.ai.provider import AIDisabledError, AIError
        with self._conn() as c:
            try:
                return fn(c, *args, home=self.home, **kwargs)
            except (AIDisabledError, AIError) as e:
                raise ApiError(str(e), status=400)


    def _feature_ref(self, c, body):
        p = self._resolve_project(c, body.get("project"))
        f = feats.get(c, body.get("ref"), p.id)
        if f is None:
            raise ApiError(f"no feature matching {body.get('ref')!r}", status=404)
        return p, f


    def _linked_repo(self, body: dict) -> tuple[str, object]:
        """Resolve the active project's linked repo or raise a clean 400.
        Returns (repo_path, project) — the lookup connection is closed here,
        so callers must not receive it."""
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            repo = gitwatch.get_link(c, p.id)
            if not repo:
                raise ApiError(f"project {p.slug} has no linked repo", status=400)
            return repo, p


    @staticmethod

    def _linked_task_for_changes(c, project_id: int) -> dict | None:
        """Best-effort link between uncommitted changes and a task.
        Priority: the RUNNING session's task (you are working on it right now),
        then the last synced commit's parsed task (#42 / task-42: ...)."""
        s = sess.current(c)
        if s is not None and s.task_id and s.project_id == project_id:
            t = tsk.get(c, s.task_id)
            if t is not None:
                return {"id": t.id, "title": t.title, "status": t.status, "source": "session"}
        st = gitwatch.status(c, project_id)
        last = st.get("last_commit") or {}
        tid = last.get("parsed_task_id")
        if tid:
            t = tsk.get(c, int(tid))
            if t is not None:
                return {"id": t.id, "title": t.title, "status": t.status,
                        "source": "commit", "commit": last.get("short")}
        return None

