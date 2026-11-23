"""devdash feature ..."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import features as core
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, guard, resolve_project, read_text_file
from ..output import emit

app = typer.Typer(help=tr("group.feature.help"))
console = Console()

_STATUS_STYLE = {"open": "dim", "proposed": "yellow", "done": "green", "rejected": "red"}


def _render_table(rows) -> None:
    table = Table(title=tr("feature.table.title"))
    for col in tr_cols("feature.cols"):
        table.add_column(col)
    for r in rows:
        p = r.get("progress", {})
        table.add_row(
            str(r["id"]), r["name"],
            f"[{_STATUS_STYLE.get(r['status'], '')}]{r['status']}[/]",
            f"{p.get('done', 0)}/{p.get('total', 0)} {tr('demo.count.tasks')} ({p.get('pct', 0)}%)",
            r.get("source", ""), (r.get("validated_at") or "")[:16].replace("T", " "),
        )
    console.print(table)


@app.command("add", help=tr("feature.add.help"))
def add_feature(
    ctx: typer.Context,
    name: str = typer.Argument(..., help=tr("opt.name_feat")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    description: str = typer.Option("", "--description", "-d", help=tr("opt.description")),
    desc_file: str | None = typer.Option(None, "--desc-file", help=tr("opt.desc_file")),
) -> None:
    """Create a feature in a project (default: active)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    f = guard(core.create, conn, proj.id, name, read_text_file(desc_file) if desc_file else description)
    data = {**f.to_dict(), "project": proj.slug, "progress": core.progress(conn, f.id)}
    emit(ctx, data, lambda: console.print(
        tr("feature.created", name=f.name, slug=proj.slug)
    ))


@app.command("list", help=tr("feature.list.help"))
def list_features(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    status: str | None = typer.Option(None, "--status", "-s", help=tr("opt.filter_status")),
) -> None:
    """List features with their task progress."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    rows = core.list(conn, project_id=pid, status=status)
    emit(ctx, {"count": len(rows), "features": rows}, lambda: _render_table(rows))


@app.command("show", help=tr("feature.show.help"))
def show_feature(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Feature detail: progress, linked tasks, linked commits."""
    from devdash.core import gitwatch
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = core.get(conn, ref, pid)
    if f is None:
        raise typer.BadParameter(tr("err.no_feature", ref=ref))
    prog = core.progress(conn, f.id)
    repo = gitwatch.get_link(conn, f.project_id)
    linked_commits = []
    if repo:
        linked_commits = [c.to_dict() for c in gitwatch.commits(conn, repo_path=repo)
                          if c.parsed_feature and c.parsed_feature.lower() == f.name.lower()]
    data = {**f.to_dict(), "progress": prog, "commits": linked_commits,
            "completion_signal": core.check_completion(conn, f.id)}

    def render() -> None:
        console.print(tr("feature.show.line", name=f.name, style=_STATUS_STYLE.get(f.status, ""),
                         status=f.status, description=f.description))
        console.print(tr("feature.show.tasks", done=prog["done"], total=prog["total"], pct=prog["pct"]))
        for t in prog["tasks"]:
            console.print(tr("feature.show.task_line", id=t["id"], status=t["status"], title=t["title"]))
        console.print(tr("feature.show.commits", n=len(linked_commits)))
        sig = data["completion_signal"]
        console.print(tr("feature.show.signal", yesno=tr("yes") if sig["all_done"] else tr("no")))

    emit(ctx, data, render)


@app.command("link", help=tr("feature.link.help"))
def link_tasks(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
    task_ids: list[int] = typer.Argument(..., help=tr("opt.task_ids")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Attach tasks to a feature."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = None
    for tid in task_ids:
        f = guard(core.attach, conn, ref, tid, pid)
    emit(ctx, {"feature": f.to_dict(), "linked": task_ids}, lambda: console.print(
        tr("feature.linked", n=len(task_ids), name=f.name)
    ))


@app.command("unlink", help=tr("feature.unlink.help"))
def unlink_task(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
    task_id: int = typer.Argument(..., help=tr("opt.task_id")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Detach a task from a feature."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = guard(core.detach, conn, ref, task_id, pid)
    emit(ctx, {"feature": f.to_dict(), "unlinked": task_id}, lambda: console.print(
        tr("feature.unlinked", id=task_id, name=f.name)
    ))


@app.command("done", help=tr("feature.done.help"))
def done_feature(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    note: str = typer.Option("", "--note", "-n", help=tr("opt.note_val")),
) -> None:
    """MANUAL validation: mark a feature as done. Human decision, recorded with a timestamp."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = guard(core.approve, conn, ref, pid, note=note)
    emit(ctx, f.to_dict(), lambda: console.print(
        tr("feature.validated", name=f.name, at=f.validated_at)
    ))


@app.command("approve", help=tr("feature.approve.help"))
def approve_feature(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.pending")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    note: str = typer.Option("", "--note", "-n", help=tr("opt.note")),
) -> None:
    """Approve a (e.g. AI) proposal: the feature becomes done."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = guard(core.approve, conn, ref, pid, note=note)
    emit(ctx, f.to_dict(), lambda: console.print(
        tr("feature.approved", name=f.name, at=f.validated_at)
    ))


@app.command("reject", help=tr("feature.reject.help"))
def reject_feature(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
    feedback: str = typer.Option("", "--feedback", "-f", help=tr("opt.feedback")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Reject a proposal; feedback is stored so it is not proposed again in a loop."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = guard(core.reject, conn, ref, feedback=feedback, project_id=pid)
    emit(ctx, f.to_dict(), lambda: console.print(
        tr("feature.rejected", name=f.name)
    ))


@app.command("propose", help=tr("feature.propose.help"))
def propose_feature(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
    confidence: float = typer.Option(0.8, "--confidence", "-c", help=tr("opt.confidence")),
    reasoning: str = typer.Option("", "--reasoning", "-r", help=tr("opt.reasoning")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Record a proposal (used by the AI assistant). NEVER marks done."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = guard(core.propose, conn, ref, pid, source="ai", confidence=confidence, reasoning=reasoning)
    emit(ctx, f.to_dict(), lambda: console.print(
        tr("feature.proposed", name=f.name, conf=confidence)
    ))


@app.command("reopen", help=tr("feature.reopen.help"))
def reopen_feature(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Reopen a feature (back to 'open')."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = guard(core.reopen, conn, ref, pid)
    emit(ctx, f.to_dict(), lambda: console.print(tr("feature.reopened", name=f.name)))


@app.command("edit", help=tr("feature.edit.help"))
def edit_feature(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
    name: str | None = typer.Option(None, "--name", "-n", help=tr("opt.name_feat")),
    description: str | None = typer.Option(None, "--description", "-d", help=tr("opt.description")),
    desc_file: str | None = typer.Option(None, "--desc-file", help=tr("opt.desc_file")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Rename / edit a feature (name stays unique within the project)."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = guard(core.update, conn, ref, pid, name=name, description=(read_text_file(desc_file) if desc_file else description))
    emit(ctx, f.to_dict(), lambda: console.print(tr("feature.updated", name=f.name)))


@app.command("delete", help=tr("feature.delete.help"))
def delete_feature(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    yes: bool = typer.Option(False, "--yes", "-y", help=tr("opt.yes")),
) -> None:
    """Permanently delete a feature. Linked tasks are detached, not deleted."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    f = core.get(conn, ref, pid)
    if f is None:
        raise typer.BadParameter(tr("err.no_feature", ref=ref))
    if not yes and not typer.confirm(tr("feature.delete.confirm", name=f.name)):
        raise typer.Abort()
    guard(core.delete, conn, f.id, pid)
    emit(ctx, {"deleted": f.id, "name": f.name},
         lambda: console.print(tr("feature.deleted", name=f.name)))
