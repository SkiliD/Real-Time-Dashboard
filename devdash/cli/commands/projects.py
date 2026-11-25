# devdash projects ...

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import projects as core
from devdash.core import tasks as core_tasks
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, guard, read_text_file, resolve_project
from ..output import emit, fmt_date

app = typer.Typer(help=tr("group.projects.help"))
console = Console()

_ENGINES = list(core.SUGGESTED_ENGINES)  # single source of truth: core.projects
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
                fmt_date(p.created_at), style=style,
            )
        console.print(table)
        if active is None:
            console.print(tr("projects.list.no_active"))

    emit(ctx, data, render)


@app.command("create", help=tr("projects.create.help"))
def create_project(
    ctx: typer.Context,
    name: str = typer.Argument(..., help=tr("opt.name")),
    description: str = typer.Option("", "--description", "-d", help=tr("opt.description")),
    desc_file: str | None = typer.Option(None, "--desc-file", help=tr("opt.desc_file")),
    engine: str = typer.Option("other", "--engine", "-e",
                                help=tr("opt.engine", items=", ".join(_ENGINES))),
    color: str = typer.Option("#8a8f98", "--color", "-c", help=tr("opt.color")),
    lang: list[str] = typer.Option([], "--lang", "-l", help=tr("opt.langs")),
    status: str = typer.Option("idea", "--status", "-s",
                                help=tr("opt.status", items=", ".join(_STATUSES))),
    set_active: bool = typer.Option(False, "--set-active", help=tr("opt.set_active")),
) -> None:
    """Create a new project."""
    conn = get_conn(ctx)
    project = guard(core.create, conn, name, description=read_text_file(desc_file) if desc_file else description, engine=engine,
                    color=color, status=status, languages=lang)
    if set_active:
        guard(core.set_active, conn, project.id)
    emit(ctx, project.to_dict(), lambda: console.print(
        tr("projects.created", slug=project.slug, engine=project.engine, status=project.status)
        + (tr("projects.created.active_suffix") if set_active else "")
    ))


@app.command("connect", help=tr("projects.connect.help"))
def connect_project(
    ctx: typer.Context,
    path: str = typer.Argument(..., help=tr("opt.path_folder")),
    name: str = typer.Option("", "--name", "-n", help=tr("opt.name")),
    description: str = typer.Option("", "--description", "-d", help=tr("opt.description")),
    engine: str = typer.Option("", "--engine", "-e",
                                help=tr("opt.engine", items=", ".join(_ENGINES)) + " " + tr("opt.engine_auto")),
    color: str = typer.Option("#8a8f98", "--color", "-c", help=tr("opt.color")),
    lang: list[str] = typer.Option([], "--lang", "-l", help=tr("opt.langs")),
    status: str = typer.Option("idea", "--status", "-s",
                                help=tr("opt.status", items=", ".join(_STATUSES))),
    set_active: bool = typer.Option(False, "--set-active", help=tr("opt.set_active")),
    no_git: bool = typer.Option(False, "--no-git", help=tr("opt.no_git")),
) -> None:
    """Connect DevDash to an EXISTING project folder: engine auto-detected from
    marker files, name suggested from the folder, git repo auto-linked (read-only)."""
    from devdash.core import detect, gitwatch
    from devdash.core import config as cfgmod

    conn = get_conn(ctx)
    info = detect.detect_project(path)
    if not info["is_dir"]:
        raise typer.BadParameter(tr("err.not_a_dir", path=path))
    eng = (engine or info["engine"] or "other").lower()
    if eng not in _ENGINES:
        raise typer.BadParameter(tr("err.bad_engine", engine=eng, items=", ".join(_ENGINES)))
    project = guard(core.create, conn, name or info["name"] or info["path"],
                    description=description, engine=eng, color=color, status=status,
                    languages=lang, path=info["path"])
    if set_active:
        guard(core.set_active, conn, project.id)
    git_root = None
    if not no_git and info["git_root"]:
        git_root = guard(gitwatch.link, conn, project.id, info["git_root"])
        guard(gitwatch.sync_repo, conn, git_root, cfgmod.load_config().commit_patterns)
    emit(ctx, {"project": project.to_dict(), "detected": info, "git_root": git_root},
         lambda: console.print(
             tr("projects.connected", name=project.name, engine=project.engine,
                path=info["path"], git=git_root or tr("projects.connected.no_git"))
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
        if project.languages:
            console.print(tr("projects.show.langs", langs=", ".join(project.languages)))
        if project.description:
            console.print(project.description)
        console.print(tr("projects.show.tasks", todo=by_status["todo"],
                         in_progress=by_status["in_progress"], done=by_status["done"],
                         cancelled=by_status["cancelled"]))

    emit(ctx, data, render)


@app.command("open", help=tr("projects.open.help"))
def open_project(
    ctx: typer.Context,
    ref: str | None = typer.Argument(None, help=tr("opt.project_default")),
) -> None:
    """Open the project's CONNECTED folder in the OS file explorer (explicit
    user action; only the server-stored path is ever opened, argv, no shell)."""
    from devdash.core import sysopen

    conn = get_conn(ctx)
    project = resolve_project(conn, ref)
    if not project.path:
        raise typer.BadParameter(tr("err.no_project_path", slug=project.slug))
    if not Path(project.path).is_dir():
        raise typer.BadParameter(tr("err.project_path_gone", path=project.path))
    try:
        sysopen.open_in_explorer(project.path)
    except OSError as e:
        raise typer.BadParameter(str(e))
    emit(ctx, {"project": project.slug, "opened": project.path},
         lambda: console.print(tr("projects.opened", path=project.path)))


@app.command("update", help=tr("projects.update.help"))
def update_project(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_project")),
    name: str | None = typer.Option(None, "--name", help=tr("opt.name")),
    description: str | None = typer.Option(None, "--description", "-d", help=tr("opt.description")),
    desc_file: str | None = typer.Option(None, "--desc-file", help=tr("opt.desc_file")),
    engine: str | None = typer.Option(None, "--engine", "-e", help=tr("opt.engine", items=", ".join(_ENGINES))),
    color: str | None = typer.Option(None, "--color", "-c", help=tr("opt.color")),
    lang: list[str] | None = typer.Option(None, "--lang", "-l", help=tr("opt.langs")),
    status: str | None = typer.Option(None, "--status", "-s",
                                       help=tr("opt.status", items=", ".join(_STATUSES))),
) -> None:
    """Update project fields (e.g. --status beta)."""
    conn = get_conn(ctx)
    project = guard(core.update, conn, int(ref) if ref.isdigit() else ref, name=name,
                    languages=lang,
                    description=(read_text_file(desc_file) if desc_file else description), engine=engine, color=color, status=status)
    emit(ctx, project.to_dict(), lambda: console.print(
        tr("projects.updated", slug=project.slug, status=project.status)
    ))


@app.command("archive", help=tr("projects.archive.help"))
def archive_project(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_project")),
    unarchive: bool = typer.Option(False, "--unarchive", help=tr("opt.unarchive")),
) -> None:
    """Soft-archive a project (hidden from list, data kept; never deleted)."""
    conn = get_conn(ctx)
    project = guard(core.update, conn, int(ref) if ref.isdigit() else ref,
                    archived=False if unarchive else True)
    state = tr("state.restored") if unarchive else tr("state.archived")
    emit(ctx, project.to_dict(), lambda: console.print(
        tr("projects.archived", slug=project.slug, state=state)
    ))


@app.command("delete", help=tr("projects.delete.help"))
def delete_project(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_project")),
    yes: bool = typer.Option(False, "--yes", "-y", help=tr("opt.yes")),
) -> None:
    """Permanently delete a project AND its tasks/devlog/sessions/features.
    Asks for confirmation unless --yes. Imported git commits are kept."""
    conn = get_conn(ctx)
    project = core.get(conn, int(ref) if ref.isdigit() else ref)
    if project is None:
        raise typer.BadParameter(tr("err.no_project", ref=ref))
    if not yes and not typer.confirm(tr("projects.delete.confirm", slug=project.slug)):
        raise typer.Abort()
    guard(core.delete, conn, project.id)
    emit(ctx, {"deleted": project.slug}, lambda: console.print(
        tr("projects.deleted", slug=project.slug)
    ))
