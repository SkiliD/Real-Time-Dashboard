# sessions endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from devdash.core import sessions as sess

class SessionsMixin:
    def session_start(self, body: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                s = sess.start(
                    c, p.id,
                    task_id=self._int(body.get("task_id"), "task_id"),
                    kind=self._str(body, "kind", "focus") or "focus",
                    minutes=self._int(body.get("minutes"), "minutes"),
                )
            except (KeyError, ValueError) as e:
                raise ApiError(str(e).strip("'\""))
            return {**s.to_dict(), "project": p.slug}


    def session_stop(self, body: dict) -> dict:
        with self._conn() as c:
            try:
                s = sess.stop(c, self._int(body.get("id"), "id"), note=self._str(body, "note"))
            except KeyError as e:
                # "no running session" is a client error (400), not a missing row
                msg = str(e).strip("'\"")
                raise ApiError(msg, status=400 if "running" in msg else 404)
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
            pid = self._list_pid(c, query)  # defaults to the ACTIVE project
            rows = sess.list(
                c, project_id=pid,
                task_id=self._int(query.get("task"), "task"),
                today=query.get("today") in ("1", "true", "yes"),
                week=query.get("week") in ("1", "true", "yes"),
                limit=self._int(query.get("limit"), "limit", 50, hi=self.MAX_LIST_LIMIT),
            )
            return {"count": len(rows), "sessions": [r.to_dict() for r in rows]}


    def sessions_minutes(self, query: dict) -> dict:
        """Aggregated per-task minutes (active project by default): the kanban
        time badges need totals, not hundreds of session rows on every poll."""
        with self._conn() as c:
            pid = self._list_pid(c, query)
            if pid is None:
                rows = c.execute(
                    "SELECT task_id, COALESCE(SUM(duration_min), 0) AS m FROM sessions "
                    "WHERE task_id IS NOT NULL GROUP BY task_id"
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT task_id, COALESCE(SUM(duration_min), 0) AS m FROM sessions "
                    "WHERE project_id = ? AND task_id IS NOT NULL GROUP BY task_id",
                    (pid,),
                ).fetchall()
            return {"minutes": {r["task_id"]: r["m"] for r in rows}}


