"""`devdash log ...` commands (step e): the devlog (dated, searchable journal)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import logs as core
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, guard, resolve_project
from ..output import emit

app = typer.Typer(help=tr("group.log.help"))
console = Console()


@app.command("add", help=tr("log.add.help"))
def add_log(
    ctx: typer.Context,
    content: str = typer.Argument(..., help=tr("opt.content")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    task: int | None = typer.Option(None, "--task", "-t", help=tr("opt.task_attach")),
    tag: list[str] = typer.Option([], "--tag", help=tr("opt.tag_rep")),
) -> None:
    """Add a devlog entry to the active project (or --project)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    entry = guard(core.add, conn, content, project_id=proj.id, task_id=task, tags=tag)
    data = {**entry.to_dict(), "project": proj.slug}

    def render() -> None:
        console.print(tr("log.added", id=entry.id, slug=proj.slug,
                         task_suffix=tr("session.task_suffix", task=task) if task else ""))

    emit(ctx, data, render)


def _render_log_table(entries) -> None:
    table = Table(title=tr("log.table.title"))
    for col in tr_cols("log.cols"):
        table.add_column(col)
    for e in entries:
        content = e.content if len(e.content) <= 80 else e.content[:77] + "…"
        table.add_row(
            str(e.id), e.created_at[:16].replace("T", " "),
            ",".join(e.tags), f"#{e.task_id}" if e.task_id else "", content,
        )
    console.print(table)


@app.command("recent", help=tr("log.recent.help"))
def recent_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p"),
    task: int | None = typer.Option(None, "--task", "-t"),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """Most recent devlog entries (alias: list)."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    rows = core.list(conn, project_id=pid, task_id=task, limit=limit)
    emit(ctx, {"count": len(rows), "entries": [r.to_dict() for r in rows]},
         lambda: _render_log_table(rows))


app.command("list")(recent_cmd)


@app.command("search", help=tr("log.search.help"))
def search_cmd(
    ctx: typer.Context,
    text: str = typer.Argument(..., help=tr("opt.query")),
    project: str | None = typer.Option(None, "--project", "-p"),
    limit: int = typer.Option(100, "--limit", "-n"),
) -> None:
    """Search devlog entries (substring, case-insensitive)."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    rows = core.search(conn, text, project_id=pid, limit=limit)
    emit(ctx, {"query": text, "count": len(rows), "entries": [r.to_dict() for r in rows]},
         lambda: _render_log_table(rows))
