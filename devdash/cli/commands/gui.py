"""`devdash gui` command (step c): launch the graphical interface."""

from __future__ import annotations

import typer

from devdash.core.i18n import tr


def gui(
    ctx: typer.Context,
    browser: bool = typer.Option(
        False, "--browser",
        help=tr("opt.browser"),
    ),
    port: int | None = typer.Option(None, "--port", "-p", help=tr("opt.port")),
) -> None:
    """Launch the DevDash GUI: native desktop window (PyWebView), or --browser."""
    try:
        from devdash.gui.desktop import run
    except ImportError:
        raise typer.BadParameter(tr("err.no_pywebview"))
    run(port=port, browser=browser)
