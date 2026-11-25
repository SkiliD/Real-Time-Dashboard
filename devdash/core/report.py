"""Markdown period reports (week/month) built from LOCAL data only.

`devdash report` / GUI button / MCP `devdash_report`. Everything comes from the
local DB (sessions, tasks, logs, git commit cache, stats); the optional AI week
summary is appended only when explicitly requested (`ai=True`) and configured —
and a failure there never fails the report.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from . import gitwatch, projects, stats, tasks as tsk
from .i18n import tr
from .models import day_start_utc, local_now, now_iso

PERIODS = {"week": 7, "month": 30}

REPORT_COMMIT_LIMIT = 500   # commits listed in the report section
REPORT_LOG_LIMIT = 50       # devlog entries listed in the report section
LOG_SNIPPET_LEN = 140       # devlog content truncated to this length
TOP_ESTIMATES = 5           # biggest estimate overruns listed


def fmt_min(m: float | None) -> str:
    """90 -> '1h30', 45 -> '45 min' (report-friendly)."""
    m = int(round(m or 0))
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h}h{r:02d}" if r else f"{h}h"


def _bar(minutes: float, max_minutes: float, width: int = 16) -> str:
    if max_minutes <= 0:
        return ""
    n = max(1, round(width * minutes / max_minutes)) if minutes > 0 else 0
    return "█" * min(width, n)


def _section_header(p, period: str, start: str, end: str) -> list[str]:
    period_label = tr("report.period." + period)
    return [
        f"# {tr('report.h1', name=p.name)}",
        "",
        f"_{period_label} : {start} → {end} — {tr('report.generated', at=local_now().strftime('%Y-%m-%d %H:%M'))}_",
        "",
    ]


def _section_summary(minutes: float, days: int, streak: int, prog: dict, done_count: int) -> list[str]:
    return [
        f"## {tr('report.summary')}",
        f"- ⏱ {tr('report.minutes', m=fmt_min(minutes))} ({tr('report.per_day', m=fmt_min(minutes / days))})",
        f"- ✔ {tr('report.tasks_done', n=done_count)}",
        f"- 🔥 {tr('report.streak', n=streak)}",
        f"- 📈 {tr('report.progress', pct=prog['pct_weighted'], done=prog['done'], total=prog['total'])}",
        "",
    ]


def _section_time_by_day(per_day: list[dict], done_by_day: list[dict]) -> list[str]:
    lines = ["", f"## {tr('report.time_day')}", ""]
    max_m = max((d["minutes"] for d in per_day), default=0)
    for d, done in zip(per_day, done_by_day):
        lines.append(f"- `{d['date']}` {_bar(d['minutes'], max_m)} {fmt_min(d['minutes'])}"
                     + (f" · ✔{done['count']}" if done["count"] else ""))
    return lines


def _section_done(done_tasks: list, est: dict) -> list[str]:
    lines = ["", f"## {tr('report.done_list')} ({len(done_tasks)})", ""]
    for t in sorted(done_tasks, key=lambda x: x.completed_at or ""):
        extra = ""
        est_row = next((r for r in est["tasks"] if r["id"] == t.id), None)
        if est_row and est_row["estimate_min"] is not None:
            extra = f" — {tr('report.est_vs', est=fmt_min(est_row['estimate_min']), act=fmt_min(est_row['actual_min']))}"
        lines.append(f"- #{t.id} {t.title}{extra}")
    if not done_tasks:
        lines.append(f"- _{tr('report.none')}_")
    return lines


def _section_commits(commits: list) -> list[str]:
    lines = ["", f"## {tr('report.commits')} ({len(commits)})", ""]
    for c in commits:
        link = f" → #{c.parsed_task_id}" if c.parsed_task_id else (f" → {c.parsed_feature}" if c.parsed_feature else "")
        lines.append(f"- `{c.short}` {c.message} — {c.author}{link}")
    if not commits:
        lines.append(f"- _{tr('report.none')}_")
    return lines


def _section_logs(log_rows: list) -> list[str]:
    lines = ["", f"## {tr('report.logs')} ({len(log_rows)})", ""]
    for r in log_rows:
        day = (r["created_at"] or "")[:10]
        content = (r["content"] or "").replace("\n", " ").strip()
        task = f" → #{r['task_id']}" if r["task_id"] else ""
        lines.append(f"- `{day}` {content[:LOG_SNIPPET_LEN]}{task}")
    if not log_rows:
        lines.append(f"- _{tr('report.none')}_")
    return lines


def _section_estimates(est: dict) -> list[str]:
    agg = est["agg"]
    lines = ["", f"## {tr('report.estimates')}", ""]
    if agg["with_estimate"]:
        pct = agg["pct_of_estimate"]
        lines.append(
            f"- {tr('report.est_line', n=agg['with_estimate'], est=fmt_min(agg['est_total_min']),
                   act=fmt_min(agg['actual_on_estimated_min']),
                   pct=(f'{pct}%' if pct is not None else '—'))}"
        )
        lines.append(f"- {tr('report.est_over', over=agg['over'], under=agg['under'])}")
        top = [r for r in est["tasks"] if r["ratio"] is not None][:TOP_ESTIMATES]
        for r in top:
            lines.append(f"  - #{r['id']} {r['title']} — {tr('report.est_vs', est=fmt_min(r['estimate_min']), act=fmt_min(r['actual_min']))} (×{r['ratio']})")
    else:
        lines.append(f"- _{tr('report.no_estimate')}_")
    return lines


def _section_ai(conn: sqlite3.Connection, home: Path | None, lines: list[str]) -> tuple[list[str], str | None]:
    """Append the optional AI summary; a failure never fails the report."""
    try:
        from .ai import assistant
        ai_text = assistant.summarize_week(conn, home=home)["summary"]
        return lines + ["", f"## {tr('report.ai')}", "", ai_text], ai_text
    except Exception as e:  # AI unavailable/disabled: the report still stands
        return lines + ["", f"## {tr('report.ai')}", "", f"_{tr('report.ai_unavailable', err=str(e)[:120])}_"], None


def period_report(
    conn: sqlite3.Connection,
    project_id: int,
    home: Path | None = None,
    period: str = "week",
    ai: bool = False,
) -> dict:
    """Build the markdown report. Raises KeyError for an unknown project,
    ValueError for an unknown period."""
    if period not in PERIODS:
        raise ValueError(tr("err.period_invalid", period=period, items=sorted(PERIODS)))
    p = projects.get(conn, project_id)
    if p is None:
        raise KeyError(tr("err.no_project_id", id=project_id))
    days = PERIODS[period]
    cutoff = day_start_utc(days - 1)  # same local-day boundary as stats
    today = local_now().date()
    start = (today - timedelta(days=days - 1)).isoformat()
    end = today.isoformat()

    minutes = stats.period_minutes(conn, p.id, days)
    per_day = stats.time_by_day(conn, p.id, days)
    done_by_day = stats.tasks_done_by_day(conn, p.id, days)
    streak = stats.streak_days(conn, p.id)
    prog = stats.project_progress(conn, p.id)
    est = stats.estimates(conn, p.id)
    done_tasks = [
        t for t in tsk.list(conn, project_id=p.id, status="done", include_cancelled=False)
        if t.completed_at and t.completed_at >= cutoff
    ]
    repo = gitwatch.get_link(conn, p.id)
    commits = [
        c for c in gitwatch.commits(conn, repo_path=repo, project_id=p.id, limit=REPORT_COMMIT_LIMIT)
        if c.committed_at >= cutoff
    ]
    log_rows = conn.execute(
        "SELECT content, created_at, task_id FROM log_entries "
        "WHERE project_id = ? AND created_at >= ? AND deleted_at IS NULL "
        "ORDER BY created_at DESC LIMIT ?",
        (p.id, cutoff, REPORT_LOG_LIMIT),
    ).fetchall()

    lines = _section_header(p, period, start, end)
    lines += _section_summary(minutes, days, streak, prog, len(done_tasks))
    lines += _section_time_by_day(per_day, done_by_day)
    lines += _section_done(done_tasks, est)
    lines += _section_commits(commits)
    lines += _section_logs(log_rows)
    lines += _section_estimates(est)
    ai_text = None
    if ai:
        lines, ai_text = _section_ai(conn, home, lines)
    md = "\n".join(lines) + "\n"
    return {
        "markdown": md,
        "project": p.slug,
        "project_name": p.name,
        "period": period,
        "days": days,
        "start": start,
        "end": end,
        "generated_at": now_iso(),
        "ai": ai_text is not None,
    }
