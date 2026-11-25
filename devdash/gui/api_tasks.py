# tasks endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from devdash.core import tasks as tsk

class TasksMixin:
    def tasks(self, query: dict) -> dict:
        with self._conn() as c:
            pid = self._list_pid(c, query)  # defaults to the ACTIVE project
            raw_status = query.get("status")
            statuses = [s for s in raw_status.split(",") if s] if raw_status else None
            # always bounded server-side (the GUI polls this every 1.5 s with all=1)
            limit = self._int(query.get("limit"), "limit", self.MAX_LIST_LIMIT, hi=self.MAX_LIST_LIMIT)
            result = tsk.list(
                c, project_id=pid, status=statuses,
                priority=query.get("priority") or None,
                tag=query.get("tag") or None,
                feature_id=self._int(query.get("feature"), "feature"),
                include_cancelled=query.get("all") in ("1", "true", "yes"),
                q=query.get("q") or None,
                limit=limit,
                trash=query.get("deleted") in ("1", "true", "yes"),  # corbeille view
            )
            return {"count": len(result), "tasks": [t.to_dict() for t in result]}


    def tasks_tree(self, query: dict) -> dict:
        """Nested task hierarchy (subtasks stacked under their parent, each node
        carrying a recursive progress rollup). Same filters as tasks()."""
        with self._conn() as c:
            pid = self._list_pid(c, query)
            raw_status = query.get("status")
            statuses = [s for s in raw_status.split(",") if s] if raw_status else None
            roots = tsk.tree(
                c, project_id=pid, status=statuses,
                include_cancelled=query.get("all") in ("1", "true", "yes"),
                q=query.get("q") or None,
            )
            return {"count": len(roots), "tree": roots}


    def tasks_update(self, body: dict) -> dict:
        """Edit a task's fields. Only keys present in the body are changed;
        explicit null clears parent_id / feature_id (set to NULL)."""
        tid = self._int(body.get("id"), "id")
        if tid is None:
            raise ApiError("id is required")
        kwargs = {}
        for key in ("title", "description", "priority"):  # status changes go through /api/tasks/status
            if key in body:
                kwargs[key] = self._str(body, key)  # wrong type -> clean 400, not a 500
        if "estimate_min" in body:
            kwargs["estimate_min"] = self._int(body["estimate_min"], "estimate_min")
        if "tags" in body:
            kwargs["tags"] = self._tags(body["tags"])
        if "parent_id" in body:
            kwargs["parent_id"] = self._int(body["parent_id"], "parent_id")  # None clears
        if "feature_id" in body:
            kwargs["feature_id"] = self._int(body["feature_id"], "feature_id")
        with self._conn() as c:
            try:
                t = tsk.update(c, tid, **kwargs)
            except KeyError as e:
                # relay the core message verbatim ("no task #N" vs "no parent task #M")
                raise ApiError(str(e).strip("'\"") or f"no task #{tid}", status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return t.to_dict()


    def tasks_delete(self, body: dict) -> dict:
        """Delete a task. Default = SOFT delete (corbeille, restorable);
        permanent=true = hard delete (subtasks cascade). Explicit user action."""
        tid = self._int(body.get("id"), "id")
        if tid is None:
            raise ApiError("id is required")
        permanent = bool(body.get("permanent"))
        with self._conn() as c:
            try:
                t = tsk.delete(c, tid, permanent=permanent)
            except KeyError:
                raise ApiError(f"no task #{tid}", status=404)
            return {"deleted": t.id, "title": t.title, "permanent": permanent}


    def tasks_restore(self, body: dict) -> dict:
        """Restore a soft-deleted task (and the subtasks deleted with it)."""
        tid = self._int(body.get("id"), "id")
        if tid is None:
            raise ApiError("id is required")
        with self._conn() as c:
            try:
                t = tsk.restore(c, tid)
            except KeyError:
                raise ApiError(f"no task #{tid}", status=404)
            return {"restored": t.id, "title": t.title}


    def tasks_add(self, body: dict) -> dict:
        title = self._str(body, "title", required=True).strip()
        if not title:
            raise ApiError("title is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                t = tsk.add(
                    c, p.id, title,
                    description=self._str(body, "description"),
                    priority=self._str(body, "priority", "medium") or "medium",
                    estimate_min=self._int(body.get("estimate_min"), "estimate_min"),
                    tags=self._tags(body.get("tags")),
                    parent_id=self._int(body.get("parent_id"), "parent_id"),
                    feature_id=self._int(body.get("feature_id"), "feature_id"),  # link at creation
                )
            except (KeyError, ValueError) as e:
                raise ApiError(str(e).strip("'\""))
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
            except KeyError as e:
                raise ApiError(str(e).strip("'\"") or f"no task #{tid}", status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return t.to_dict()


