# stats endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from devdash.core import projects as proj
from devdash.core import stats
from devdash.core import tasks as tsk

DAYS_DEFAULT = 14   # default /api/stats window
DAYS_MAX = 365      # hard cap on the ?days= parameter
DAYS_WEEK = 7       # "week" bucket of the minutes summary
DAYS_MONTH = 30     # "month" bucket
DAYS_ALL = 3650     # "all" bucket = 10 years back, effectively everything

class StatsMixin:
    def stats_overview(self, query: dict) -> dict:
        with self._conn() as c:
            project = query.get("project")
            explicit_all = project in ("all", "*")
            if explicit_all:
                pid = None  # aggregate across ALL projects
            elif project:
                pid = self._resolve_project(c, project).id
            else:
                active = proj.get_active(c)
                pid = active.id if active else None
            days = self._int(query.get("days"), "days", DAYS_DEFAULT)
            if days is None or not (1 <= days <= DAYS_MAX):
                raise ApiError(f"days must be between 1 and {DAYS_MAX}")
            group = (query.get("group") or "day").lower()
            if group not in ("day", "week", "month"):
                raise ApiError("group must be day, week or month")
            if pid is None and not explicit_all:
                return {"project": None, "group": group, "days": days, "time_by_day": [],
                        "tasks_done_by_day": [], "by_project": stats.minutes_by_project(c, days),
                        "minutes": {"today": 0, "week": 0, "month": 0, "all": 0},
                        "streak_days": 0, "progress": None, "by_tag": [], "tasks": {"open": 0, "done": 0}}
            all_tasks = tsk.list(c, project_id=pid, include_cancelled=False)
            time_rows = stats.time_by_day(c, pid, days)
            done_rows = stats.tasks_done_by_day(c, pid, days)
            return {
                "project": pid,
                "group": group,
                "days": days,
                # regrouped in place when group != day: same keys, label in "date"
                "time_by_day": stats.regroup_rows(time_rows, group, "minutes"),
                "tasks_done_by_day": stats.regroup_rows(done_rows, group, "count"),
                "by_project": stats.minutes_by_project(c, days),
                "minutes": {
                    "today": stats.today_minutes(c, pid),
                    "week": stats.period_minutes(c, pid, DAYS_WEEK),
                    "month": stats.period_minutes(c, pid, DAYS_MONTH),
                    "all": stats.period_minutes(c, pid, DAYS_ALL),
                },
                "streak_days": stats.streak_days(c, pid),
                "progress": stats.project_progress(c, pid, all_tasks) if pid is not None else None,
                "by_tag": stats.by_tag(c, pid, days=days),  # bound the session scan to the period
                "tasks": {
                    "open": sum(1 for t in all_tasks if t.status in ("todo", "in_progress")),
                    "done": sum(1 for t in all_tasks if t.status == "done"),
                },
            }

    # --- AI assistant --------------------------------------


    def stats_estimates(self, query: dict) -> dict:
        """Estimate vs actual minutes per task + aggregate (?project= like /api/stats)."""
        with self._conn() as c:
            pid = self._list_pid(c, query)
            return {"project": pid, **stats.estimates(c, pid)}

