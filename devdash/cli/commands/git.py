from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from devdash.core import gitwatch
from devdash.core.i18n import tr, tr_cols

STATUS_MSG_SNIPPET = 60  # commit message chars shown in `git status`
from ..deps import get_conn, guard, resolve_project
from ..output import emit, fmt_ts

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
    except gitwatch.GitError as e:  # not a repo / path missing
        raise typer.BadParameter(str(e))
    emit(ctx, {"project": proj.slug, "repo_path": repo},
         lambda: console.print(tr("git.linked", slug=proj.slug, repo=repo)))


@app.command("ignore", help=tr("git.ignore.help"))
def ignore_cmd(
    ctx: typer.Context,
    repo: str | None = typer.Option(None, "--repo", help=tr("opt.repo_path")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Add/refresh the DevDash block in the .gitignore of the linked repo
    (idempotent, marker-delimited) so local DevDash data is never committed.
    This is the only file DevDash ever writes in your repository, only on
    this explicit command — git history stays untouched (read-only rule)."""
    from devdash.core import config as cfgmod

    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    target = repo or gitwatch.get_link(conn, proj.id)
    if not target:
        raise typer.BadParameter(tr("err.no_linked_repo", slug=proj.slug))
    result = guard(gitwatch.ensure_gitignore, target, home=cfgmod.devdash_home())
    result["project"] = proj.slug
    emit(ctx, result, lambda: console.print(
        tr("git.ignore.already" if not result["updated"] else "git.ignore.done",
           path=result["path"], n=result["lines"])
    ))


@app.command("unlink", help=tr("git.unlink.help"))
def unlink_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Unlink the git repository of a project (cached commits are kept)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    guard(gitwatch.unlink, conn, proj.id)
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
        result = {"repos": [guard(gitwatch.sync_repo, conn, repo, cfgmod.load_config().commit_patterns)], "errors": []}
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
        table.add_row(c.short, fmt_ts(c.committed_at), c.author,
                      c.message if len(c.message) <= STATUS_MSG_SNIPPET else c.message[:STATUS_MSG_SNIPPET - 3] + "…", link)
    console.print(table)


@app.command("run", help=tr("git.run.help"))
def run_cmd(
    ctx: typer.Context,
    cmd: str = typer.Argument(..., help=tr("opt.git_cmd")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Run a READ-ONLY git command in the linked repo (allowlist: log, diff, status...)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    repo = gitwatch.get_link(conn, proj.id)
    if not repo:
        raise typer.BadParameter(tr("git.no_repo", slug=proj.slug))
    r = gitwatch.run_readonly(repo, cmd)

    def render() -> None:
        if r["ok"]:
            console.print(r["stdout"] or r["stderr"])
        else:
            console.print(f"[red]{r['stderr']}[/red]")

    emit(ctx, r, render)


def _remote_read_cmd(ctx, cmdline: str, project: str | None) -> None:
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    repo = gitwatch.get_link(conn, proj.id)
    if not repo:
        raise typer.BadParameter(tr("git.no_repo", slug=proj.slug))
    r = gitwatch.run_remote_read(repo, cmdline)

    def render() -> None:
        if r["ok"]:
            console.print(r["stdout"] or r["stderr"] or "[green]ok[/]")
        else:
            console.print(f"[red]{r['stderr']}[/red]")

    emit(ctx, r, render)


@app.command("fetch", help=tr("git.fetch.help"))
def fetch_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Update remote-tracking refs of the linked repo (working tree untouched)."""
    _remote_read_cmd(ctx, "fetch", project)


@app.command("pull", help=tr("git.pull.help"))
def pull_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """Fast-forward the linked repo only (--ff-only; other update modes refused)."""
    _remote_read_cmd(ctx, "pull --ff-only", project)


@app.command("remote", help=tr("git.remote.help"))
def remote_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
) -> None:
    """List the configured remotes of the linked repo (remote -v, read-only)."""
    _remote_read_cmd(ctx, "remote -v", project)


@app.command("log", help=tr("git.log.help"))
def log_cmd(
    ctx: typer.Context,
    task: int | None = typer.Option(None, "--task", "-t", help=tr("opt.task_commits")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    unparsed: bool = typer.Option(False, "--unparsed", help=tr("opt.unparsed")),
    limit: int = typer.Option(50, "--limit", "-n", help=tr("opt.limit")),
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
            console.print(tr("git.status.last", short=last["short"], message=last["message"][:STATUS_MSG_SNIPPET],
                             date=fmt_ts(last["committed_at"])))

    emit(ctx, data, render)
