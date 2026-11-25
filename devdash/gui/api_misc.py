# misc endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

import json

from .api_common import ApiError
from devdash.core import events as ev
from devdash.core import gitwatch
from devdash.core import projects as proj
from devdash.core import report as report_mod
from devdash.core import sessions as sess
from devdash.core import stats
from devdash.core import tasks as tsk
from devdash.core import timeline as tl

EVENT_PAYLOAD_MAX = 2000  # serialized payload chars kept per event in API responses


def _trunc_event_payloads(rows: list[dict]) -> list[dict]:
    """Cap serialized event payloads: the feed only renders a one-line detail,
    but full payloads (a task dict can carry a 100k-char description) would be
    hundreds of KB transferred + JSON.stringify'd on every poll for nothing."""
    for r in rows:
        p = r.get("payload")
        if isinstance(p, str):
            if len(p) > EVENT_PAYLOAD_MAX:
                r["payload"] = p[:EVENT_PAYLOAD_MAX] + "…"
        elif isinstance(p, dict):
            s = json.dumps(p, ensure_ascii=False)
            if len(s) > EVENT_PAYLOAD_MAX:
                r["payload"] = {"_truncated": True, "preview": s[:200]}
    return rows


class MiscMixin:
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
                "done_today": stats.tasks_done_today(c, active.id),
                "session": None if s is None else {
                    **s.to_dict(), "elapsed_min": sess.elapsed_min(s),
                },
                "today_minutes": stats.today_minutes(c, active.id),
                "streak_days": stats.streak_days(c, active.id),
                "progress": stats.project_progress(c, active.id),
                "git": gitwatch.status(c, active.id),
                "last_event_id": ev.last_id(c),
            }


    def timeline(self, query: dict) -> dict:
        """Activity feed (event bus), newest first. ?project= (default active,
        'all' = everything), ?type=task|session|log|git|feature|project, ?limit=."""
        with self._conn() as c:
            pid = self._list_pid(c, query)
            entity = (query.get("type") or query.get("entity") or "").strip() or None
            if entity is not None and entity not in tl.ENTITIES:
                raise ApiError(f"invalid type {entity!r}, expected one of {list(tl.ENTITIES)}")
            limit = self._int(query.get("limit"), "limit", 150, hi=self.MAX_LIST_LIMIT) or 150
            rows = tl.list(c, project_id=pid, entity=entity, limit=limit)
            return {"count": len(rows), "project": pid, "events": _trunc_event_payloads(rows)}


    def report(self, body: dict) -> dict:
        """Markdown period report (week/month) of a project. POST because ai=true
        may trigger an (optional, configured) cloud call for the week summary."""
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            period = (self._str(body, "period") or "week").lower()
            if period not in report_mod.PERIODS:
                raise ApiError(f"period must be one of {sorted(report_mod.PERIODS)}")
            return report_mod.period_report(
                c, p.id, home=self.home, period=period, ai=bool(body.get("ai")),
            )


    def events_since(self, since) -> dict:
        with self._conn() as c:
            since_id = self._int(since, "since", 0)
            if since_id < 0:
                raise ApiError("invalid since: must be >= 0")
            got = ev.since(c, since_id)
            # last_id must be the last event actually RETURNED: with the LIMIT
            # in ev.since, the global max would silently skip the missing ones
            last = got[-1].id if got else since_id
            return {"events": _trunc_event_payloads([e.to_dict() for e in got]), "last_id": last}

