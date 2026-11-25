# logs endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError, LIST_CONTENT_MAX, truncate_field
from devdash.core import logs

class LogsMixin:
    def logs_list(self, query: dict) -> dict:
        with self._conn() as c:
            pid = self._list_pid(c, query)  # defaults to the ACTIVE project
            rows = logs.list(
                c, project_id=pid,
                task_id=self._int(query.get("task"), "task"),
                query=query.get("q") or None,
                limit=self._int(query.get("limit"), "limit", 200, hi=self.MAX_LIST_LIMIT),
                trash=query.get("deleted") in ("1", "true", "yes"),  # corbeille view
            )
            # list rows carry a truncated content (polled every 1.5 s);
            # the full text is served by /api/logs/show
            return {"count": len(rows),
                    "entries": [truncate_field(r.to_dict(), "content", LIST_CONTENT_MAX) for r in rows]}


    def logs_show(self, query: dict) -> dict:
        """Full log entry (id required): inline edit must not save back a
        truncated content over the full text."""
        eid = self._int(query.get("id"), "id")
        if eid is None:
            raise ApiError("id is required")
        with self._conn() as c:
            e = logs.get(c, eid)
            if e is None or e.deleted_at is not None:
                raise ApiError("log not found", status=404)
            return {"entry": e.to_dict()}


    def logs_add(self, body: dict) -> dict:
        content = self._str(body, "content", required=True).strip()
        if not content:
            raise ApiError("content is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                e = logs.add(c, content, project_id=p.id,
                             task_id=self._int(body.get("task_id"), "task_id"),
                             tags=self._tags(body.get("tags")))
            except KeyError as e2:
                raise ApiError(str(e2).strip("'\""))
            except ValueError as e:
                raise ApiError(str(e))
            return {**e.to_dict(), "project": p.slug}


    def logs_update(self, body: dict) -> dict:
        eid = self._int(body.get("id"), "id")
        if eid is None:
            raise ApiError("id is required")
        with self._conn() as c:
            try:
                e = logs.update(c, eid,
                                content=self._str(body, "content") if "content" in body else None,
                                tags=self._tags(body["tags"]) if "tags" in body else None)
            except KeyError:
                raise ApiError(f"no log entry #{eid}", status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return e.to_dict()


    def logs_delete(self, body: dict) -> dict:
        """Delete a devlog entry. Default = SOFT delete (corbeille, restorable);
        permanent=true = hard delete. Explicit user action."""
        eid = self._int(body.get("id"), "id")
        if eid is None:
            raise ApiError("id is required")
        permanent = bool(body.get("permanent"))
        with self._conn() as c:
            try:
                e = logs.delete(c, eid, permanent=permanent)
            except KeyError:
                raise ApiError(f"no log entry #{eid}", status=404)
            return {"deleted": e.id, "permanent": permanent}


    def logs_restore(self, body: dict) -> dict:
        """Restore a soft-deleted devlog entry."""
        eid = self._int(body.get("id"), "id")
        if eid is None:
            raise ApiError("id is required")
        with self._conn() as c:
            try:
                e = logs.restore(c, eid)
            except KeyError:
                raise ApiError(f"no log entry #{eid}", status=404)
            return {"restored": e.id}


