"""`devdash projects ...` commands (step b)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import projects as core
from devdash.core import tasks as core_tasks
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, guard
from ..output import emit

app = typer.Typer(help=tr("group.projects.help"))
console = Console()

_ENGINES = ["unity", "unreal", "godot", "web", "other"]
_STATUSES = list(core.VALID_STATUSES)


def _dot(color: str) -> str:
    return f"[bold {color}]●[/]"


@app.command("list", help=tr("projects.list.help"))
def list_projects(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", "-a", help=tr("opt.all_archived")),
) -> None:
    """List all projects; the active one is marked with *."""
    conn = get_conn(ctx)
    projs = core.list(conn, include_archived=all)
    active = core.get_active(conn)
    data = {
        "active": active.slug if active else None,
        "projects": [p.to_dict() for p in projs],
    }

    def render() -> None:
        table = Table(title=tr("projects.table.title"))
        for col in tr_cols("projects.cols"):
            table.add_column(col)
        for p in projs:
            marker = "[green]*[/]" if active and p.id == active.id else ""
            n_open = len(core_tasks.list(conn, project_id=p.id, status=["todo", "in_progress"]))
            style = "dim" if p.archived else ""
            table.add_row(
                marker, p.slug, p.name, p.engine, p.status, str(n_open),
                p.created_at[:10], style=style,
            )
        console.print(table)
        if active is None:
            console.print(tr("projects.list.no_active"))

    emit(ctx, data, render)


@app.command("create", help=tr("projects.create.help"))
def create_project(
    ctx: typer.Context,
    name: str = typer.Argument(..., help=tr("opt.name")),
    description: str = typer.Option("", "--description", "-d"),
    engine: str = typer.Option("other", "--engine", "-e",
                                help=tr("opt.engine", list=", ".join(_ENGINES))),
    color: str = typer.Option("#8a8f98", "--color", "-c", help=tr("opt.color")),
    status: str = typer.Option("idea", "--status", "-s", help=tr("opt.status", list=", ".join(_STATUSES))),
    set_active: bool = typer.Option(False, "--set-active", help=tr("opt.set_active")),
) -> None:
    """Create a new project."""
    conn = get_conn(ctx)
    project = core.create(conn, name, description=description, engine=engine,
                          color=color, status=status)
    if set_active:
        core.set_active(conn, project.id)
    emit(ctx, project.to_dict(), lambda: console.print(
        tr("projects.created", slug=project.slug, engine=project.engine, status=project.status)
        + (tr("projects.created.active_suffix") if set_active else "")
    ))


@app.command("set-active", help=tr("projects.set_active.help"))
def set_active(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_project")),
) -> None:
    """Switch the active project (lossless: other projects keep all their data)."""
    conn = get_conn(ctx)
    project = guard(core.set_active, conn, ref)
    emit(ctx, {"active": project.to_dict()}, lambda: console.print(
        tr("projects.active_now", slug=project.slug, name=project.name)
    ))


@app.command("show", help=tr("projects.show.help"))
def show_project(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_project")),
) -> None:
    """Project details + task counters."""
    conn = get_conn(ctx)
    project = core.get(conn, int(ref) if ref.isdigit() else ref)
    if project is None:
        raise typer.BadParameter(tr("err.no_project", ref=ref))
    all_tasks = core_tasks.list(conn, project_id=project.id)
    by_status = {s: sum(1 for t in all_tasks if t.status == s)
                 for s in core_tasks.VALID_STATUSES}
    active = core.get_active(conn)
    data = {**project.to_dict(), "is_active": active is not None and active.id == project.id,
            "task_counts": by_status}

    def render() -> None:
        console.print(f"[bold {project.color}]●[/] [bold]{project.name}[/] "
                      f"([dim]{project.slug}[/]) — {project.engine} · {project.status}")
        if project.description:
            console.print(project.description)
        console.print(tr("projects.show.tasks", todo=by_status["todo"],
                         in_progress=by_status["in_progress"], done=by_status["done"],
                         cancelled=by_status["cancelled"]))

    emit(ctx, data, render)


@app.command("update", help=tr("projects.update.help"))
def update_project(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_project")),
    name: str | None = typer.Option(None, "--name"),
    description: str | None = typer.Option(None, "--description", "-d"),
    engine: str | None = typer.Option(None, "--engine", "-e"),
    color: str | None = typer.Option(None, "--color", "-c"),
    status: str | None = typer.Option(None, "--status", "-s"),
) -> None:
    """Update project fields (e.g. --status beta)."""
    conn = get_conn(ctx)
    project = core.update(conn, int(ref) if ref.isdigit() else ref, name=name,
                          description=description, engine=engine, color=color, status=status)
    emit(ctx, project.to_dict(), lambda: console.print(
        tr("projects.updated", slug=project.slug, status=project.status)
    ))


@app.command("archive", help=tr("projects.archive.help"))
def archive_project(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_project")),
    unarchive: bool = typer.Option(False, "--unarchive"),
) -> None:
    """Soft-archive a project (hidden from list, data kept; never deleted)."""
    conn = get_conn(ctx)
    project = core.update(conn, int(ref) if ref.isdigit() else ref,
                          archived=False if unarchive else True)
    state = tr("state.restored") if unarchive else tr("state.archived")
    emit(ctx, project.to_dict(), lambda: console.print(
        tr("projects.archived", slug=project.slug, state=state)
    ))
