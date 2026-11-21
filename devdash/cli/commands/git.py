"""`devdash git ...` commands (step f). READ-ONLY: DevDash never writes to
your repository; it only runs `git log` / `git rev-parse`.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import gitwatch
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, resolve_project
from ..output import emit

app = typer.Typer(help=tr("group.git.help"))
console = Console()


@app.command("link", help=tr("git.link.help"))
def link_cmd(
    ctx: typer.Context,
    path: str = typer.Argument(..., help=tr("opt.path")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Link a local git repository to a project (read-only access)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    try:
        repo = gitwatch.link(conn, proj.id, path)
    except (gitwatch.GitError, KeyError) as e:
        raise typer.BadParameter(str(e))
    emit(ctx, {"project": proj.slug, "repo_path": repo},
         lambda: console.print(tr("git.linked", slug=proj.slug, repo=repo)))


@app.command("unlink", help=tr("git.unlink.help"))
def unlink_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Unlink the git repository of a project (cached commits are kept)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    gitwatch.unlink(conn, proj.id)
    emit(ctx, {"project": proj.slug, "linked": False},
         lambda: console.print(tr("git.unlinked", slug=proj.slug)))


@app.command("sync", help=tr("git.sync.help"))
def sync_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.one_or_all")),
) -> None:
    """Sync commits of linked repos (read-only `git log`) into the local cache."""
    from devdash.core import config as cfgmod
    conn = get_conn(ctx)
    if project:
        proj = resolve_project(conn, project)
        repo = gitwatch.get_link(conn, proj.id)
        if repo is None:
            raise typer.BadParameter(tr("err.no_repo", slug=proj.slug))
        result = {"repos": [gitwatch.sync_repo(conn, repo, cfgmod.load_config().commit_patterns)], "errors": []}
    else:
        result = gitwatch.sync_all(conn)
    data = result

    def render() -> None:
        for r in result["repos"]:
            console.print(tr("git.sync.line", repo=r["repo_path"], branch=r["branch"],
                             new=r["new"], total=r["total"]))
        for e in result["errors"]:
            console.print(tr("git.sync.error", repo=e["repo_path"], error=e["error"]))

    emit(ctx, data, render)


def _render_commits(rows) -> None:
    table = Table(title=tr("git.table.title"))
    for col in tr_cols("git.cols"):
        table.add_column(col)
    for c in rows:
        link = f"#{c.parsed_task_id}" if c.parsed_task_id else (f"feat:{c.parsed_feature}" if c.parsed_feature else "[dim]—[/]")
        table.add_row(c.short, c.committed_at[:16].replace("T", " "), c.author,
                      c.message if len(c.message) <= 60 else c.message[:57] + "…", link)
    console.print(table)


@app.command("log", help=tr("git.log.help"))
def log_cmd(
    ctx: typer.Context,
    task: int | None = typer.Option(None, "--task", "-t", help=tr("opt.task_commits")),
    project: str | None = typer.Option(None, "--project", "-p"),
    unparsed: bool = typer.Option(False, "--unparsed", help=tr("opt.unparsed")),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """List cached commits (use --task <id> for `devdash git log --task <id>`)."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    repo = gitwatch.get_link(conn, pid) if pid else None
    rows = gitwatch.commits(conn, repo_path=repo, project_id=pid, task_id=task,
                            unparsed=unparsed, limit=limit)
    emit(ctx, {"count": len(rows), "commits": [c.to_dict() for c in rows]},
         lambda: _render_commits(rows))


@app.command("status", help=tr("git.status.help"))
def status_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Git snapshot of a project: branch, last commit, commits today."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    data = gitwatch.status(conn, proj.id)
    data["project"] = proj.slug

    def render() -> None:
        if not data.get("linked"):
            console.print(tr("git.status.unlinked", slug=proj.slug))
            return
        last = data.get("last_commit") or {}
        console.print(
            tr("git.status.line", slug=proj.slug, repo=data["repo_path"], branch=data.get("branch"),
               today=data.get("commits_today"), cached=data.get("cached_commits"))
        )
        if last:
            console.print(tr("git.status.last", short=last["short"], message=last["message"][:60],
                             date=last["committed_at"][:16]))

    emit(ctx, data, render)
