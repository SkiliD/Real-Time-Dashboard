"""`devdash update` — update DevDash easily after a pull (or another install mode).

  devdash update           apply the update (git pull --ff-only + dep refresh)
  devdash update --check   only report whether an update is available
  devdash update status    install mode + local git info (no network)
"""

from __future__ import annotations

import typer
from rich.console import Console

from devdash import __version__
from devdash.core import updater
from devdash.core.i18n import tr
from ..deps import guard
from ..output import emit

app = typer.Typer(help=tr("group.update.help"))
console = Console()


def _render(result: dict) -> None:
    console.print(tr("update.done" if result.get("ok") else "update.fail",
                     steps="; ".join(result.get("steps", [])) or "-",
                     head=result.get("head", "-"),
                     error=result.get("error", ""),
                     note=result.get("note", "")))


@app.callback(invoke_without_command=True)
def update(
    ctx: typer.Context,
    check_only: bool = typer.Option(False, "--check", help=tr("opt.update_check")),
    no_reinstall: bool = typer.Option(False, "--no-reinstall", help=tr("opt.no_reinstall")),
) -> None:
    """Update DevDash. Git-checkout installs: `git pull --ff-only` + `pip install -e .`.
    pip/pipx/executable installs: prints the right command instead. Never
    force-pulls; aborts on a dirty working tree (you commit yourself)."""
    if ctx.invoked_subcommand is not None:
        return
    if check_only:
        result = guard(updater.check_for_updates)
        emit(ctx, result, lambda: console.print(tr("update.check.line", **{
            k: result.get(k) for k in ("update_available", "behind", "ahead", "local", "remote", "error", "note")
            if result.get(k) is not None})))
        return
    result = guard(updater.update, reinstall=not no_reinstall)
    emit(ctx, result, lambda: _render(result))


@app.command("check", help=tr("update.check.help"))
def check(ctx: typer.Context) -> None:
    """Fetch origin and report whether an update is available (no changes made)."""
    result = guard(updater.check_for_updates)
    emit(ctx, result, lambda: console.print(tr("update.check.line", **{
        k: result.get(k) for k in ("update_available", "behind", "ahead", "local", "remote", "error", "note")
        if result.get(k) is not None})))


@app.command("status", help=tr("update.status.help"))
def status_cmd(ctx: typer.Context) -> None:
    """Show install mode + local git info (no network)."""
    result = {"version": __version__, **updater.git_status()}
    emit(ctx, result, lambda: console.print(tr("update.status.line", **{
        k: result.get(k) for k in ("version", "mode", "head", "branch", "dirty", "remote")
        if result.get(k) is not None})))
