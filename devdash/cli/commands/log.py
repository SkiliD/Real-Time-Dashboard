"""devdash log ... -- the devlog

If it breaks, the tests will say so.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import logs as core
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, guard, resolve_project, read_text_file
from ..output import emit

app = typer.Typer(help=tr("group.log.help"))
console = Console()


@app.command("add", help=tr("log.add.help"))
def add_log(
    ctx: typer.Context,
    content: str = typer.Argument("", help=tr("opt.content")),
    content_file: str | None = typer.Option(None, "--content-file", help=tr("opt.content_file")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    task: int | None = typer.Option(None, "--task", "-t", help=tr("opt.task_attach")),
    tag: list[str] = typer.Option([], "--tag", help=tr("opt.tag_rep")),
) -> None:
    """Add a devlog entry to the active project (or --project)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    if content_file:
        content = read_text_file(content_file)
    if not content.strip():
        raise typer.BadParameter("content is required (argument or --content-file)")
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
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    task: int | None = typer.Option(None, "--task", "-t", help=tr("opt.task")),
    limit: int = typer.Option(50, "--limit", "-n", help=tr("opt.limit")),
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
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    limit: int = typer.Option(100, "--limit", "-n", help=tr("opt.limit")),
) -> None:
    """Search devlog entries (substring, case-insensitive)."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    rows = core.search(conn, text, project_id=pid, limit=limit)
    emit(ctx, {"query": text, "count": len(rows), "entries": [r.to_dict() for r in rows]},
         lambda: _render_log_table(rows))


@app.command("edit", help=tr("log.edit.help"))
def edit_log(
    ctx: typer.Context,
    entry_id: int = typer.Argument(..., help=tr("opt.id")),
    content: str = typer.Argument("", help=tr("opt.content")),
    content_file: str | None = typer.Option(None, "--content-file", help=tr("opt.content_file")),
) -> None:
    """Replace the content of a devlog entry."""
    conn = get_conn(ctx)
    if content_file:
        content = read_text_file(content_file)
    if not content.strip():
        raise typer.BadParameter("content is required (argument or --content-file)")
    entry = guard(core.update, conn, entry_id, content=content)
    emit(ctx, entry.to_dict(), lambda: console.print(tr("log.updated", id=entry.id)))


@app.command("delete", help=tr("log.delete.help"))
def delete_log(
    ctx: typer.Context,
    entry_id: int = typer.Argument(..., help=tr("opt.id")),
    yes: bool = typer.Option(False, "--yes", "-y", help=tr("opt.yes")),
) -> None:
    """Delete a devlog entry (asks for confirmation unless --yes)."""
    conn = get_conn(ctx)
    entry = core.get(conn, entry_id)
    if entry is None:
        raise typer.BadParameter(tr("err.no_log_entry", id=entry_id))
    if not yes and not typer.confirm(tr("log.delete.confirm", id=entry_id)):
        raise typer.Abort()
    guard(core.delete, conn, entry_id)
    emit(ctx, {"deleted": entry_id}, lambda: console.print(tr("log.deleted", id=entry_id)))
