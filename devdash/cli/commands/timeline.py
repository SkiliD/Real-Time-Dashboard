# devdash timeline / devdash report -- activity feed + markdown period report
# Works on Windows, Mac and Linux.

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import report as report_mod
from devdash.core import timeline as tl
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, resolve_project
from ..output import emit, fmt_ts

console = Console()


def _local(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return fmt_ts(ts)


def timeline_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    type: str | None = typer.Option(None, "--type", "-t", help=tr("opt.tl_type")),
    limit: int = typer.Option(50, "--limit", "-n", help=tr("opt.tl_limit"), min=1, max=tl.MAX_LIMIT),
) -> None:
    """Recent activity of a project (tasks, sessions, logs, git commits...), newest first."""
    conn = get_conn(ctx)
    p = resolve_project(conn, project)
    try:
        rows = tl.list(conn, project_id=p.id, entity=type, limit=limit)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    data = {"project": p.slug, "count": len(rows), "events": rows}

    def render() -> None:
        if not rows:
            console.print(tr("timeline.empty"))
            return
        table = Table(title=tr("timeline.title", slug=p.slug))
        for col in tr_cols("timeline.cols"):
            table.add_column(col)
        for e in rows:
            table.add_row(
                _local(e["created_at"]),
                f"{e.get('icon', '•')} {e['entity']}",
                tl.label(e),
                tl.detail(e),
            )
        console.print(table)

    emit(ctx, data, render)


def report_cmd(
    ctx: typer.Context,
    period: str = typer.Option("week", "--period", help=tr("opt.report_period")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    out: str | None = typer.Option(None, "--out", "-o", help=tr("opt.report_out")),
    ai: bool = typer.Option(False, "--ai", help=tr("opt.report_ai")),
) -> None:
    """Markdown week/month report of a project (local data + git cache; --ai adds the AI summary)."""
    conn = get_conn(ctx)
    p = resolve_project(conn, project)
    try:
        data = report_mod.period_report(conn, p.id, period=period.lower(), ai=ai)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if out:
        path = Path(out).expanduser()
        path.write_text(data["markdown"], encoding="utf-8")
        data = {**data, "file": str(path)}
        emit(ctx, data, lambda: console.print(
            tr("report.written", path=str(path), n=data["markdown"].count("\n"))))
    else:
        emit(ctx, data, lambda: typer.echo(data["markdown"]))
