"""devdash task ...

Small on purpose -- it has to stay fast.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import features as feats
from devdash.core import tasks as core
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, guard, resolve_project, read_text_file
from ..output import emit

app = typer.Typer(help=tr("group.task.help"))
console = Console()

_PRIORITY_STYLE = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "green",
}
_STATUS_ICON = {"todo": "○", "in_progress": "▶", "done": "✔", "cancelled": "✖"}


def _task_row(t) -> tuple:
    return (
        str(t.id),
        _STATUS_ICON.get(t.status, "?"),
        t.priority,
        t.title,
        ",".join(t.tags),
        "" if t.estimate_min is None else f"{t.estimate_min}m",
    )


def _render_task_table(tasks_list) -> None:
    table = Table(title=tr("task.table.title"))
    for col in tr_cols("task.cols"):
        table.add_column(col)
    for t in tasks_list:
        style = _PRIORITY_STYLE.get(t.priority, "")
        table.add_row(*_task_row(t)[:2], f"[{style}]{t.priority}[/]", t.title,
                       ",".join(t.tags),
                       "" if t.estimate_min is None else f"{t.estimate_min}m")
    console.print(table)


@app.command("add", help=tr("task.add.help"))
def add_task(
    ctx: typer.Context,
    title: str = typer.Argument(..., help=tr("opt.title")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project")),
    description: str = typer.Option("", "--description", "-d"),
    desc_file: str | None = typer.Option(None, "--desc-file", help=tr("opt.desc_file")),
    priority: str = typer.Option("medium", "--priority",
                                 help=tr("opt.priority", items=", ".join(core.VALID_PRIORITIES))),
    tag: list[str] = typer.Option([], "--tag", "-t", help=tr("opt.tag")),
    est: int | None = typer.Option(None, "--est", "--estimate", help=tr("opt.est")),
    parent: int | None = typer.Option(None, "--parent", help=tr("opt.parent")),
    feature: int | None = typer.Option(None, "--feature", help=tr("opt.feature")),
) -> None:
    """Add a task to a project (default: the active project)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    task = guard(core.add, conn, proj.id, title, description=read_text_file(desc_file) if desc_file else description, priority=priority,
                 estimate_min=est, tags=tag, parent_id=parent, feature_id=feature)
    data = {**task.to_dict(), "project": proj.slug}

    def render() -> None:
        console.print(tr("task.added", id=task.id, slug=proj.slug, title=task.title))

    emit(ctx, data, render)


@app.command("list", help=tr("task.list.help"))
def list_tasks(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project")),
    status: list[str] = typer.Option([], "--status", "-s", help=tr("opt.filter_status")),
    priority: str | None = typer.Option(None, "--priority",
                                         help=tr("opt.filter_priority", items=", ".join(core.VALID_PRIORITIES))),
    tag: str | None = typer.Option(None, "--tag", "-t", help=tr("opt.filter_tag")),
    feature: str | None = typer.Option(None, "--feature", "-f", help=tr("opt.filter_feature")),
    all: bool = typer.Option(False, "--all", "-a", help=tr("opt.all_cancelled")),
    limit: int | None = typer.Option(None, "--limit", "-n", help=tr("opt.limit")),
) -> None:
    """List tasks with filters (default: all non-cancelled tasks of all projects)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project) if project else None
    feature_id = None
    if feature:
        # a feature name is scoped to the resolved (or active) project
        proj = proj or resolve_project(conn, None)
        f = feats.get(conn, feature, proj.id)
        if f is None:
            raise typer.BadParameter(tr("err.no_feature", ref=feature))
        feature_id = f.id
    result = core.list(
        conn,
        project_id=proj.id if proj else None,
        status=status or None,
        priority=priority,
        tag=tag,
        feature_id=feature_id,
        include_cancelled=all,
    )
    total = len(result)
    if limit is not None:
        if limit < 0:
            raise typer.BadParameter(tr("err.negative_limit"))
        result = result[:limit]
    data = {"count": total, "shown": len(result), "tasks": [t.to_dict() for t in result]}
    emit(ctx, data, lambda: _render_task_table(result))


@app.command("tree", help=tr("task.tree.help"))
def tree_tasks(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project")),
    status: list[str] = typer.Option([], "--status", "-s", help=tr("opt.filter_status")),
    all: bool = typer.Option(False, "--all", "-a", help=tr("opt.all_cancelled")),
) -> None:
    """Task hierarchy: subtasks stacked under their parent, with a progress rollup."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project) if project else None
    roots = core.tree(
        conn,
        project_id=proj.id if proj else None,
        status=status or None,
        include_cancelled=all,
    )

    def render() -> None:
        def walk(node: dict, prefix: str, is_last: bool, is_root: bool) -> None:
            branch = "" if is_root else ("└─ " if is_last else "├─ ")
            icon = _STATUS_ICON.get(node["status"], "?")
            style = _PRIORITY_STYLE.get(node["priority"], "")
            prog = node.get("progress") or {}
            pstr = (f" [dim]({prog['done']}/{prog['total']})[/]" if prog.get("total") else "")
            console.print(f"{prefix}{branch}[bold]#{node['id']}[/] {icon} "
                          f"[{style}]{node['priority']}[/] {node['title']}{pstr}")
            kids = node.get("children") or []
            for i, k in enumerate(kids):
                ext = "" if is_root else ("   " if is_last else "│  ")
                walk(k, prefix + ext, i == len(kids) - 1, False)
        if not roots:
            console.print(tr("task.tree.empty"))
        for i, node in enumerate(roots):
            walk(node, "", i == len(roots) - 1, True)

    emit(ctx, {"count": len(roots), "tree": roots}, render)


@app.command("show", help=tr("task.show.help"))
def show_task(ctx: typer.Context, task_id: int) -> None:
    """Show one task with its subtasks."""
    conn = get_conn(ctx)
    task = core.get(conn, task_id)
    if task is None:
        raise typer.BadParameter(tr("err.no_task", id=task_id))
    subs = core.list(conn, parent_id=task_id)
    data = {**task.to_dict(), "subtasks": [s.to_dict() for s in subs]}

    def render() -> None:
        console.print(f"[bold]#{task.id}[/] {_STATUS_ICON.get(task.status,'?')} {task.title} "
                      f"[{ _PRIORITY_STYLE.get(task.priority,'')}]{task.priority}[/]")
        if task.description:
            console.print(task.description)
        console.print(tr("task.show.meta", status=task.status, tags=task.tags,
                         est=task.estimate_min, created=task.created_at))
        for s in subs:
            console.print(tr("task.show.sub", id=s.id, title=s.title, status=s.status))

    emit(ctx, data, render)


def _set_status(ctx, task_id: int, status: str, verb_key: str) -> None:
    conn = get_conn(ctx)
    task = guard(core.set_status, conn, task_id, status)
    emit(ctx, task.to_dict(), lambda: console.print(
        tr("task.status_line", id=task.id, verb=tr(verb_key), title=task.title)
    ))


@app.command("done", help=tr("task.done.help"))
def done_task(ctx: typer.Context, task_id: int) -> None:
    """Mark a task as done."""
    _set_status(ctx, task_id, "done", "verb.done")


@app.command("start", help=tr("task.start.help"))
def start_task(ctx: typer.Context, task_id: int) -> None:
    """Start working on a task (other in-progress tasks of the project go back to todo)."""
    conn = get_conn(ctx)
    task = guard(core.start, conn, task_id)
    emit(ctx, task.to_dict(), lambda: console.print(
        tr("task.started", id=task.id, title=task.title)
    ))


@app.command("cancel", help=tr("task.cancel.help"))
def cancel_task(ctx: typer.Context, task_id: int) -> None:
    """Cancel a task."""
    _set_status(ctx, task_id, "cancelled", "verb.cancelled")


@app.command("reopen", help=tr("task.reopen.help"))
def reopen_task(ctx: typer.Context, task_id: int) -> None:
    """Move a task back to todo."""
    _set_status(ctx, task_id, "todo", "verb.reopened")


@app.command("edit", help=tr("task.edit.help"))
def edit_task(
    ctx: typer.Context,
    task_id: int,
    title: str | None = typer.Option(None, "--title", help=tr("opt.title")),
    description: str | None = typer.Option(None, "--description", "-d", help=tr("opt.description")),
    desc_file: str | None = typer.Option(None, "--desc-file", help=tr("opt.desc_file")),
    priority: str | None = typer.Option(None, "--priority",
                                         help=tr("opt.priority", items=", ".join(core.VALID_PRIORITIES))),
    est: int | None = typer.Option(None, "--est", "--estimate", help=tr("opt.est")),
    tag: list[str] = typer.Option([], "--tag", "-t", help=tr("opt.replace_tags")),
    parent: int | None = typer.Option(None, "--parent", help=tr("opt.parent")),
    unparent: bool = typer.Option(False, "--unparent", help=tr("opt.unparent")),
) -> None:
    """Edit a task's fields (only the options you pass are changed)."""
    conn = get_conn(ctx)
    # pass ONLY the options actually provided: core.update treats an explicit
    # None on a NOT NULL column as an error, and unset options as "no change"
    changes: dict = {}
    if title is not None:
        changes["title"] = title
    if desc_file is not None:
        changes["description"] = read_text_file(desc_file)
    elif description is not None:
        changes["description"] = description
    if priority is not None:
        changes["priority"] = priority
    if est is not None:
        changes["estimate_min"] = est
    if tag:
        changes["tags"] = tag
    if unparent:
        changes["parent_id"] = None  # explicit None clears the parent
    elif parent is not None:
        changes["parent_id"] = parent
    task = guard(core.update, conn, task_id, **changes)
    emit(ctx, task.to_dict(), lambda: console.print(
        tr("task.updated", id=task.id, title=task.title)
    ))


@app.command("delete", help=tr("task.delete.help"))
def delete_task(
    ctx: typer.Context,
    task_id: int = typer.Argument(..., help=tr("opt.id")),
    yes: bool = typer.Option(False, "--yes", "-y", help=tr("opt.yes")),
    permanent: bool = typer.Option(False, "--permanent", help=tr("opt.permanent")),
) -> None:
    """Delete a task: soft (trash, restorable) by default, --permanent for good.
    Subtasks follow. Asks unless --yes."""
    conn = get_conn(ctx)
    task = core.get(conn, task_id)
    if task is None:
        raise typer.BadParameter(tr("err.no_task", id=task_id))
    if not yes and not typer.confirm(tr("task.delete.confirm", id=task_id, title=task.title)):
        raise typer.Abort()
    guard(core.delete, conn, task_id, permanent=permanent)
    if permanent:
        emit(ctx, {"deleted": task_id}, lambda: console.print(tr("task.deleted", id=task_id)))
    else:
        emit(ctx, {"deleted": task_id, "soft": True},
             lambda: console.print(tr("task.delete.soft", id=task_id)))


@app.command("restore", help=tr("task.restore.help"))
def restore_task(
    ctx: typer.Context,
    task_id: int = typer.Argument(..., help=tr("opt.id")),
) -> None:
    """Restore a task from the trash (with the subtasks deleted along with it)."""
    conn = get_conn(ctx)
    task = guard(core.restore, conn, task_id)
    emit(ctx, {"restored": task.id}, lambda: console.print(tr("task.restored", id=task.id)))
