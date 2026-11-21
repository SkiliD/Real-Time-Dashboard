"""`devdash stats ...` commands (step j): time, tasks, streak, tags."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import projects, sessions, stats, tasks as tsk
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, resolve_project
from ..output import emit

app = typer.Typer(help=tr("group.stats.help"))
console = Console()


def _pid(ctx, project: str | None) -> tuple:
    conn = get_conn(ctx)
    if project:
        return conn, resolve_project(conn, project).id
    active_pid = None
    active = projects.get_active(conn)
    if active:
        active_pid = active.id
    return conn, active_pid


def _minutes_block(conn, pid, days: int) -> dict:
    return {
        "minutes": stats.period_minutes(conn, pid, days),
        "today": stats.today_minutes(conn, pid),
        "streak_days": stats.streak_days(conn, pid),
    }


@app.command("today", help=tr("stats.today.help"))
def today_cmd(ctx: typer.Context, project: str | None = typer.Option(None, "--project", "-p")) -> None:
    """Today: minutes worked, tasks done, current session, streak."""
    conn, pid = _pid(ctx, project)
    done_today = [t for t in tsk.list(conn, project_id=pid, status="done")
                  if (t.completed_at or "")[:10] == stats.today_str()]
    s = sessions.current(conn)
    data = {**_minutes_block(conn, pid, 1), "tasks_done_today": len(done_today),
            "session_running": s is not None}

    def render() -> None:
        console.print(tr("stats.today.line", today=f"{data['today']:.0f}", done=data["tasks_done_today"],
                         streak=data["streak_days"]))
        if s:
            console.print(tr("stats.session_line", id=s.id, kind=s.kind,
                             elapsed=f"{sessions.elapsed_min(s):.1f}"))

    emit(ctx, data, render)


@app.command("week", help=tr("stats.week.help"))
def week_cmd(ctx: typer.Context, project: str | None = typer.Option(None, "--project", "-p")) -> None:
    """Last 7 days: minutes and tasks done per day + totals."""
    conn, pid = _pid(ctx, project)
    time7 = stats.time_by_day(conn, pid, 7)
    done7 = stats.tasks_done_by_day(conn, pid, 7)
    data = {**_minutes_block(conn, pid, 7), "per_day": [
        {"date": t["date"], "minutes": t["minutes"], "tasks_done": d["count"]}
        for t, d in zip(time7, done7)
    ]}

    def render() -> None:
        table = Table(title=tr("stats.week.table"))
        for col in tr_cols("stats.week.cols"):
            table.add_column(col)
        for row in data["per_day"]:
            bar = "█" * min(40, int(row["minutes"] / 15))
            table.add_row(row["date"], f"{row['minutes']:.0f} {bar}", str(row["tasks_done"]))
        console.print(table)
        console.print(tr("stats.week.total", min=f"{data['minutes']:.0f}", streak=data["streak_days"]))

    emit(ctx, data, render)


@app.command("month", help=tr("stats.month.help"))
def month_cmd(ctx: typer.Context, project: str | None = typer.Option(None, "--project", "-p")) -> None:
    """Last 30 days totals."""
    conn, pid = _pid(ctx, project)
    data = {**_minutes_block(conn, pid, 30), "period_days": 30}
    emit(ctx, data, lambda: console.print(
        tr("stats.month.line", min=f"{data['minutes']:.0f}", today=f"{data['today']:.0f}",
           streak=data["streak_days"])
    ))


@app.command("streak", help=tr("stats.streak.help"))
def streak_cmd(ctx: typer.Context, project: str | None = typer.Option(None, "--project", "-p")) -> None:
    """Consecutive days with at least one finished work session."""
    conn, pid = _pid(ctx, project)
    data = {"streak_days": stats.streak_days(conn, pid), "project": pid}
    emit(ctx, data, lambda: console.print(
        tr("stats.streak.line", n=data["streak_days"])
    ))


@app.command("tags", help=tr("stats.tags.help"))
def tags_cmd(ctx: typer.Context, project: str | None = typer.Option(None, "--project", "-p")) -> None:
    """Distribution per tag: task count and minutes worked."""
    conn, pid = _pid(ctx, project)
    rows = stats.by_tag(conn, pid)
    data = {"by_tag": rows}

    def render() -> None:
        table = Table(title=tr("stats.tags.table"))
        for col in tr_cols("stats.tags.cols"):
            table.add_column(col)
        for r in rows:
            table.add_row(r["tag"], str(r["tasks"]), f"{r['minutes']:.0f}")
        console.print(table)

    emit(ctx, data, render)


@app.command("all", help=tr("stats.all.help"))
def all_cmd(ctx: typer.Context, project: str | None = typer.Option(None, "--project", "-p")) -> None:
    """Everything: today, week, month, streak, tags, progress."""
    conn, pid = _pid(ctx, project)
    data = {
        **_minutes_block(conn, pid, 30),
        "week_per_day": stats.time_by_day(conn, pid, 7),
        "month_minutes": stats.period_minutes(conn, pid, 30),
        "by_tag": stats.by_tag(conn, pid),
        "progress": stats.project_progress(conn, pid) if pid else None,
    }
    emit(ctx, data, lambda: console.print(
        tr("stats.all.line", today=f"{data['today']:.0f}",
           week=f"{stats.period_minutes(conn, pid, 7):.0f}", month=f"{data['month_minutes']:.0f}",
           streak=data["streak_days"])
    ))
