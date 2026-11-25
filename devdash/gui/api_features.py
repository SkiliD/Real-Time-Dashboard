# features endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from devdash.core import features as feats

class FeaturesMixin:
    def features_list(self, query: dict) -> dict:
        with self._conn() as c:
            pid = self._list_pid(c, query)  # defaults to the ACTIVE project
            rows = feats.list(c, project_id=pid, status=query.get("status") or None,
                              trash=query.get("deleted") in ("1", "true", "yes"))  # corbeille view
            return {"count": len(rows), "features": rows}


    def features_add(self, body: dict) -> dict:
        name = self._str(body, "name", required=True).strip()
        if not name:
            raise ApiError("name is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                f = feats.create(c, p.id, name, self._str(body, "description"))
            except (KeyError, ValueError) as e:
                raise ApiError(str(e).strip("'\""))
            return {**f.to_dict(), "project": p.slug, "progress": feats.progress(c, f.id)}


    def features_attach(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            tid = self._int(body.get("task_id"), "task_id")
            if tid is None:
                raise ApiError("task_id is required")
            try:
                f = feats.attach(c, f.id, tid)
            except KeyError as e:
                raise ApiError(str(e).strip("'\""), status=404)
            except ValueError as e:  # e.g. task belongs to another project
                raise ApiError(str(e))
            return {**f.to_dict(), "progress": feats.progress(c, f.id)}


    def features_done(self, body: dict) -> dict:
        """MANUAL validation (human decision) — the only path to done."""
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.approve(c, f.id, note=self._str(body, "note"))
            return f.to_dict()


    def features_propose(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            # validate confidence: must be a real number clamped to [0,1] (never
            # trust a raw body value — the GUI renders it as a percentage)
            conf = body.get("confidence")
            if conf is not None:
                if isinstance(conf, bool) or not isinstance(conf, (int, float)):
                    raise ApiError("confidence must be a number in [0,1]", status=400)
                conf = max(0.0, min(1.0, float(conf)))
            reasoning = body.get("reasoning")
            if reasoning is not None and not isinstance(reasoning, str):
                raise ApiError("reasoning must be a string", status=400)
            f = feats.propose(c, f.id, source=self._str(body, "source", "ai"),
                              confidence=conf, reasoning=reasoning)
            return f.to_dict()


    def features_approve(self, body: dict) -> dict:
        # alias of /api/features/done — both routes are public API, keep them in sync
        return self.features_done(body)


    def features_reject(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.reject(c, f.id, feedback=self._str(body, "feedback"))
            return f.to_dict()


    def features_reopen(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.reopen(c, f.id)
            return f.to_dict()


    def features_update(self, body: dict) -> dict:
        """Rename / edit a feature (name unique per project)."""
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            kwargs = {}
            if "name" in body:
                kwargs["name"] = self._str(body, "name")
            if "description" in body:
                kwargs["description"] = self._str(body, "description")
            try:
                f = feats.update(c, f.id, p.id, **kwargs)
            except (KeyError, ValueError) as e:
                raise ApiError(str(e).strip("'\""))
            return f.to_dict()


    def features_delete(self, body: dict) -> dict:
        """Delete a feature. Default = SOFT delete (corbeille, restorable);
        permanent=true = hard delete (tasks keep existing, detached).
        Explicit user action; the GUI confirms first."""
        permanent = bool(body.get("permanent"))
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            try:
                f = feats.delete(c, f.id, p.id, permanent=permanent)
            except KeyError as e:
                raise ApiError(str(e).strip("'\""), status=404)
            return {"deleted": f.id, "name": f.name, "permanent": permanent}


    def features_restore(self, body: dict) -> dict:
        """Restore a soft-deleted feature."""
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            try:
                f = feats.restore(c, f.id, p.id)
            except KeyError as e:
                raise ApiError(str(e).strip("'\""), status=404)
            return {"restored": f.id, "name": f.name}


    def features_detach(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            tid = self._int(body.get("task_id"), "task_id")
            if tid is None:
                raise ApiError("task_id is required")
            try:
                f = feats.detach(c, f.id, tid, project_id=p.id)
            except KeyError as e:
                raise ApiError(str(e).strip("'\""), status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return {**f.to_dict(), "progress": feats.progress(c, f.id)}


    def features_show(self, query: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, query.get("project"))
            f = feats.get(c, query.get("ref"), p.id)
            if f is None:
                raise ApiError(f"no feature matching {query.get('ref')!r}", status=404)
            return {**f.to_dict(), "progress": feats.progress(c, f.id),
                    "completion_signal": feats.check_completion(c, f.id)}


