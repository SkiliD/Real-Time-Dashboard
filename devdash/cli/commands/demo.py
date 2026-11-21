"""`devdash demo ...` commands (step k): seed demo data to explore the tool."""

from __future__ import annotations

import typer
from rich.console import Console

from devdash.core import demo as demo_core
from devdash.core import projects as core_projects
from devdash.core.i18n import tr
from ..deps import get_conn
from ..output import emit

app = typer.Typer(help=tr("group.demo.help"))
console = Console()

_COUNT_KEYS = {
    "projects": "demo.count.projects",
    "features": "demo.count.features",
    "tasks": "demo.count.tasks",
    "sessions": "demo.count.sessions",
    "log_entries": "demo.count.logs",
    "active_project": "demo.count.active",
}


@app.command("seed", help=tr("demo.seed.help"))
def seed_cmd(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help=tr("opt.force")),
) -> None:
    """Fill the database with demo data (2 projects, tasks, sessions, devlog)."""
    conn = get_conn(ctx)
    existing = core_projects.list(conn, include_archived=True)
    if existing and not force:
        raise typer.BadParameter(tr("err.demo_exists", n=len(existing)))
    summary = demo_core.seed(conn)

    def render() -> None:
        console.print(tr("demo.seeded"))
        for key, value in summary.items():
            console.print(f"   {tr(_COUNT_KEYS.get(key, key))} : [bold]{value}[/]")
        console.print(tr("demo.hint"))

    emit(ctx, summary, render)
