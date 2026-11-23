# entry point, wires up all the sub-apps

from __future__ import annotations

import typer

from devdash import __version__
from devdash.core.i18n import tr
from devdash.cli.commands import ai as ai_cmd
from devdash.cli.commands import backup as backup_cmd
from devdash.cli.commands import demo as demo_cmd
from devdash.cli.commands import feature as feature_cmd
from devdash.cli.commands import git as git_cmd
from devdash.cli.commands import log as log_cmd
from devdash.cli.commands import projects as projects_cmd
from devdash.cli.commands import session as session_cmd
from devdash.cli.commands import stats as stats_cmd
from devdash.cli.commands import status as status_cmd
from devdash.cli.commands import tasks as tasks_cmd
from devdash.cli.commands import update as update_cmd
from devdash.cli.commands.gui import gui

app = typer.Typer(
    name="devdash",
    help=tr("app.help"),
    no_args_is_help=True,
)


@app.callback()
def callback(
    ctx: typer.Context,
    json: bool = typer.Option(False, "--json", help=tr("opt.json")),
) -> None:
    ctx.obj = {"json": json}


app.add_typer(projects_cmd.app, name="projects")
app.add_typer(tasks_cmd.app, name="task")
app.add_typer(session_cmd.app, name="session")
app.add_typer(log_cmd.app, name="log")
app.add_typer(git_cmd.app, name="git")
app.add_typer(feature_cmd.app, name="feature")
app.add_typer(ai_cmd.app, name="ai")
app.add_typer(stats_cmd.app, name="stats")
app.add_typer(demo_cmd.app, name="demo")
app.add_typer(update_cmd.app, name="update")
app.command("backup", help=tr("cmd.backup.help"))(backup_cmd.backup_cmd)
app.command("export", help=tr("cmd.export.help"))(backup_cmd.export_cmd)
app.command("import", help=tr("cmd.import.help"))(backup_cmd.import_cmd)


@app.command("mcp", help=tr("cmd.mcp.help"))
def mcp_cmd() -> None:
    """Run the MCP server (stdio JSON-RPC) so agents (Claude Code, Vibe...) can query DevDash."""
    from devdash.mcp_server import serve
    serve()
app.command("status", help=tr("status.help"))(status_cmd.status_cmd)
app.command("gui", help=tr("cmd.gui.help"))(gui)


@app.command(help=tr("cmd.version"))
def version(ctx: typer.Context) -> None:
    """Print the DevDash version + git head when running from a checkout."""
    from devdash.core import updater
    from .output import emit
    st = updater.git_status()
    suffix = f" (git {st['head']} @ {st['branch']})" if st.get("git") else f" ({st['mode']})"
    data = {"version": __version__, **{k: st.get(k) for k in ("mode", "git", "head", "branch", "dirty")}}
    emit(ctx, data, lambda: typer.echo(f"devdash {__version__}{suffix}"))


def entrypoint() -> None:
    app()


if __name__ == "__main__":
    entrypoint()
