"""JSON API exposed by the GUI server. Thin wrapper over devdash.core.

One short-lived SQLite connection per call (thread-safe with the threading
HTTP server). All methods return plain dicts/lists (JSON-serializable).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from devdash.core import config, db
from devdash.core.ai import assistant
from devdash.core import events as ev
from devdash.core import features as feats
from devdash.core import gitwatch
from devdash.core import logs
from devdash.core import projects as proj
from devdash.core import sessions as sess
from devdash.core import stats
from devdash.core import tasks as tsk


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Api:
    def __init__(self, home: Path | None = None):
        self.home = home  # None -> resolved from DEVDASH_HOME at call time

    @contextmanager
    def _conn(self):
        home = self.home or config.devdash_home()
        conn = db.connect(config.db_path(home))
        db.init_db(conn)
        try:
            yield conn
        finally:
            conn.close()

    # --- projects ---------------------------------------------------------

    def projects(self) -> dict:
        with self._conn() as c:
            active = proj.get_active(c)
            return {
                "active": active.slug if active else None,
                "projects": [p.to_dict() for p in proj.list(c, include_archived=True)],
            }

    def projects_create(self, body: dict) -> dict:
        try:
            with self._conn() as c:
                p = proj.create(
                    c, body["name"],
                    description=body.get("description", ""),
                    engine=body.get("engine", "other"),
                    color=body.get("color", "#8a8f98"),
                    status=body.get("status", "idea"),
                )
                if body.get("set_active"):
                    proj.set_active(c, p.id)
                return p.to_dict()
        except (KeyError, ValueError) as e:
            raise ApiError(str(e))

    def projects_set_active(self, body: dict) -> dict:
        ref = body.get("ref")
        if ref is None:
            raise ApiError("missing 'ref'")
        with self._conn() as c:
            try:
                p = proj.set_active(c, int(ref) if str(ref).isdigit() else str(ref))
            except KeyError:
                raise ApiError(f"no project matching {ref!r}", status=404)
            return {"active": p.to_dict()}

    def projects_update(self, body: dict) -> dict:
        ref = body.get("ref")
        with self._conn() as c:
            try:
                p = proj.update(
                    c, int(ref) if str(ref).isdigit() else str(ref),
                    name=body.get("name"), description=body.get("description"),
                    engine=body.get("engine"), color=body.get("color"),
                    status=body.get("status"),
                    archived=body.get("archived"),
                )
            except KeyError:
                raise ApiError(f"no project matching {ref!r}", status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return p.to_dict()

    # --- tasks ------------------------------------------------------------

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

    def tasks(self, query: dict) -> dict:
        with self._conn() as c:
            project = query.get("project")
            pid = self._resolve_project(c, project).id if project else None
            raw_status = query.get("status")
            statuses = [s for s in raw_status.split(",") if s] if raw_status else None
            result = tsk.list(
                c, project_id=pid, status=statuses,
                priority=query.get("priority") or None,
                tag=query.get("tag") or None,
                include_cancelled=query.get("all") in ("1", "true", "yes"),
            )
            return {"count": len(result), "tasks": [t.to_dict() for t in result]}

    def tasks_add(self, body: dict) -> dict:
        title = body.get("title", "").strip()
        if not title:
            raise ApiError("title is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            t = tsk.add(
                c, p.id, title,
                description=body.get("description", ""),
                priority=body.get("priority", "medium"),
                estimate_min=body.get("estimate_min"),
                tags=body.get("tags", []),
                parent_id=body.get("parent_id"),
            )
            return {**t.to_dict(), "project": p.slug}

    def tasks_set_status(self, body: dict) -> dict:
        try:
            tid = int(body["id"])
            status = body["status"]
        except (KeyError, ValueError):
            raise ApiError("body must contain {id, status}")
        with self._conn() as c:
            try:
                t = tsk.start(c, tid) if status == "in_progress" else tsk.set_status(c, tid, status)
            except KeyError:
                raise ApiError(f"no task #{tid}", status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return t.to_dict()

    # --- stats (step j) --------------------------------------------------------

    def stats_overview(self, query: dict) -> dict:
        with self._conn() as c:
            project = query.get("project")
            if project:
                pid = self._resolve_project(c, project).id
            else:
                active = proj.get_active(c)
                pid = active.id if active else None
            days = int(query.get("days", "14"))
            if pid is None:
                return {"project": None, "time_by_day": [], "tasks_done_by_day": [],
                        "minutes": {"today": 0, "week": 0, "month": 0, "all": 0},
                        "streak_days": 0, "progress": None, "by_tag": [], "tasks": {"open": 0, "done": 0}}
            all_tasks = tsk.list(c, project_id=pid, include_cancelled=False)
            return {
                "project": pid,
                "time_by_day": stats.time_by_day(c, pid, days),
                "tasks_done_by_day": stats.tasks_done_by_day(c, pid, 7),
                "minutes": {
                    "today": stats.today_minutes(c, pid),
                    "week": stats.period_minutes(c, pid, 7),
                    "month": stats.period_minutes(c, pid, 30),
                    "all": stats.period_minutes(c, pid, 3650),
                },
                "streak_days": stats.streak_days(c, pid),
                "progress": stats.project_progress(c, pid),
                "by_tag": stats.by_tag(c, pid),
                "tasks": {
                    "open": sum(1 for t in all_tasks if t.status in ("todo", "in_progress")),
                    "done": sum(1 for t in all_tasks if t.status == "done"),
                },
            }

    # --- AI assistant (step i, optional) --------------------------------------

    def ai_status(self) -> dict:
        from devdash.core.ai import ai_status as _ai_status
        return _ai_status(config.load_config(self.home))

    def _ai_call(self, fn, *args, **kwargs):
        from devdash.core.ai.provider import AIDisabledError, AIError
        with self._conn() as c:
            try:
                return fn(c, *args, home=self.home, **kwargs)
            except (AIDisabledError, AIError) as e:
                raise ApiError(str(e), status=400)

    def ai_ask(self, body: dict) -> dict:
        q = (body.get("question") or "").strip()
        if not q:
            raise ApiError("question is required")
        return self._ai_call(assistant.ask, q)

    def ai_summarize(self, body: dict) -> dict:
        return self._ai_call(assistant.summarize_week)

    def ai_plan(self, body: dict) -> dict:
        title = (body.get("title") or "").strip()
        if not title:
            raise ApiError("title is required")
        # apply=True is the explicit human consent (GUI confirm button)
        return self._ai_call(assistant.plan, title, apply=bool(body.get("apply")))

    def ai_verdict(self, body: dict) -> dict:
        ref = body.get("ref")
        if not ref:
            raise ApiError("ref is required")
        return self._ai_call(assistant.feature_verdict, ref)

    # --- features (step h) ---------------------------------------------------

    def features_list(self, query: dict) -> dict:
        with self._conn() as c:
            project = query.get("project")
            pid = self._resolve_project(c, project).id if project else None
            rows = feats.list(c, project_id=pid, status=query.get("status") or None)
            return {"count": len(rows), "features": rows}

    def features_add(self, body: dict) -> dict:
        name = (body.get("name") or "").strip()
        if not name:
            raise ApiError("name is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            f = feats.create(c, p.id, name, body.get("description", ""))
            return {**f.to_dict(), "project": p.slug, "progress": feats.progress(c, f.id)}

    def _feature_ref(self, c, body):
        p = self._resolve_project(c, body.get("project"))
        f = feats.get(c, body.get("ref"), p.id)
        if f is None:
            raise ApiError(f"no feature matching {body.get('ref')!r}", status=404)
        return p, f

    def features_attach(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.attach(c, f.id, int(body["task_id"]))
            return {**f.to_dict(), "progress": feats.progress(c, f.id)}

    def features_done(self, body: dict) -> dict:
        """MANUAL validation (human decision) — the only path to done."""
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.approve(c, f.id, note=body.get("note", ""))
            return f.to_dict()

    def features_propose(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.propose(c, f.id, source=body.get("source", "ai"),
                              confidence=body.get("confidence"), reasoning=body.get("reasoning"))
            return f.to_dict()

    def features_approve(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.approve(c, f.id, note=body.get("note", ""))
            return f.to_dict()

    def features_reject(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.reject(c, f.id, feedback=body.get("feedback", ""))
            return f.to_dict()

    def features_reopen(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.reopen(c, f.id)
            return f.to_dict()

    def features_show(self, query: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, query.get("project"))
            f = feats.get(c, query.get("ref"), p.id)
            if f is None:
                raise ApiError(f"no feature matching {query.get('ref')!r}", status=404)
            return {**f.to_dict(), "progress": feats.progress(c, f.id),
                    "completion_signal": feats.check_completion(c, f.id)}

    # --- git (step f, read-only) -------------------------------------------

    def git_link(self, body: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                repo = gitwatch.link(c, p.id, body["path"])
            except (gitwatch.GitError, KeyError) as e:
                raise ApiError(str(e))
            return {"project": p.slug, "repo_path": repo}

    def git_unlink(self, body: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            gitwatch.unlink(c, p.id)
            return {"project": p.slug, "linked": False}

    def git_sync(self, body: dict) -> dict:
        with self._conn() as c:
            if body.get("project"):
                p = self._resolve_project(c, body["project"])
                repo = gitwatch.get_link(c, p.id)
                if repo is None:
                    raise ApiError(f"project {p.slug} has no linked repo", status=400)
                return {"repos": [gitwatch.sync_repo(c, repo, config.load_config(self.home).commit_patterns)],
                        "errors": []}
            return gitwatch.sync_all(c, home=self.home)

    def git_commits(self, query: dict) -> dict:
        with self._conn() as c:
            project = query.get("project")
            pid = self._resolve_project(c, project).id if project else None
            repo = gitwatch.get_link(c, pid) if pid else None
            rows = gitwatch.commits(
                c, repo_path=repo, project_id=pid,
                task_id=int(query["task"]) if query.get("task") else None,
                unparsed=query.get("unparsed") in ("1", "true", "yes"),
                limit=int(query.get("limit", "100")),
            )
            return {"count": len(rows), "commits": [r.to_dict() for r in rows]}

    def git_status(self, query: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, query.get("project"))
            data = gitwatch.status(c, p.id)
            data["project"] = p.slug
            return data

    # --- devlog (step e) ---------------------------------------------------

    def logs_list(self, query: dict) -> dict:
        with self._conn() as c:
            project = query.get("project")
            pid = self._resolve_project(c, project).id if project else None
            rows = logs.list(
                c, project_id=pid,
                task_id=int(query["task"]) if query.get("task") else None,
                query=query.get("q") or None,
                limit=int(query.get("limit", "200")),
            )
            return {"count": len(rows), "entries": [r.to_dict() for r in rows]}

    def logs_add(self, body: dict) -> dict:
        content = (body.get("content") or "").strip()
        if not content:
            raise ApiError("content is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            e = logs.add(c, content, project_id=p.id,
                         task_id=body.get("task_id"), tags=body.get("tags", []))
            return {**e.to_dict(), "project": p.slug}

    # --- sessions (step d) -------------------------------------------------

    def session_start(self, body: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            s = sess.start(
                c, p.id,
                task_id=body.get("task_id"),
                kind=body.get("kind", "focus"),
                minutes=body.get("minutes"),
            )
            return {**s.to_dict(), "project": p.slug}

    def session_stop(self, body: dict) -> dict:
        with self._conn() as c:
            try:
                s = sess.stop(c, body.get("id"), note=body.get("note", ""))
            except KeyError as e:
                raise ApiError(str(e), status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return s.to_dict()

    def session_current(self) -> dict:
        with self._conn() as c:
            s = sess.current(c)
            if s is None:
                return {"running": False, "session": None, "elapsed_min": 0}
            return {"running": True, "session": s.to_dict(),
                    "elapsed_min": sess.elapsed_min(s)}

    def sessions_list(self, query: dict) -> dict:
        with self._conn() as c:
            project = query.get("project")
            pid = self._resolve_project(c, project).id if project else None
            rows = sess.list(
                c, project_id=pid,
                task_id=int(query["task"]) if query.get("task") else None,
                today=query.get("today") in ("1", "true", "yes"),
                week=query.get("week") in ("1", "true", "yes"),
                limit=int(query.get("limit", "50")),
            )
            return {"count": len(rows), "sessions": [r.to_dict() for r in rows]}

    # --- status / events (realtime) ----------------------------------------

    def status(self) -> dict:
        """Snapshot for the GUI header: active project, open tasks, running session."""
        with self._conn() as c:
            active = proj.get_active(c)
            if active is None:
                return {"active": None, "session": None, "last_event_id": ev.last_id(c)}
            open_tasks = tsk.list(c, project_id=active.id, status=["todo", "in_progress"])
            in_progress = [t.to_dict() for t in open_tasks if t.status == "in_progress"]
            s = sess.current(c)
            if s is not None and s.project_id != active.id:
                s = None
            return {
                "active": active.to_dict(),
                "open_tasks": len(open_tasks),
                "in_progress": in_progress,
                "session": None if s is None else {
                    **s.to_dict(), "elapsed_min": sess.elapsed_min(s),
                },
                "today_minutes": stats.today_minutes(c, active.id),
                "streak_days": stats.streak_days(c, active.id),
                "progress": stats.project_progress(c, active.id),
                "last_event_id": ev.last_id(c),
            }

    def events_since(self, since: int) -> dict:
        with self._conn() as c:
            got = ev.since(c, int(since))
            return {"events": [e.to_dict() for e in got], "last_id": ev.last_id(c)}
