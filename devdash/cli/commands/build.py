# devdash build -- create the standalone executable (PyInstaller wrapper)

from __future__ import annotations

import typer
from rich.console import Console
from rich.markup import escape

from devdash.core import build as build_core
from devdash.core.i18n import tr
from ..deps import guard
from ..output import emit

console = Console()


def build_cmd(
    ctx: typer.Context,
    onefile: bool = typer.Option(False, "--onefile", help=tr("opt.build_onefile")),
    no_gui: bool = typer.Option(False, "--no-gui", help=tr("opt.build_no_gui")),
    no_console: bool = typer.Option(False, "--no-console", help=tr("opt.build_no_console")),
) -> None:
    """Build the standalone DevDash executable (default: dist/devdash/devdash.exe).
    Needs PyInstaller: pip install -e ".[build]". Local build, no network."""
    result = guard(build_core.build_exe, onefile=onefile, no_gui=no_gui, no_console=no_console)

    def render() -> None:
        # escape: the error text may contain "[...]" (e.g. ".[build]") which
        # rich would otherwise parse as markup and swallow
        if result.get("ok"):
            console.print(tr("build.done", exe=escape(result.get("exe") or "-")))
        else:
            console.print(tr("build.fail", error=escape(result.get("error") or "?")))

    emit(ctx, result, render)
