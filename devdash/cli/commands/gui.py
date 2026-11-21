"""`devdash gui` command (step c): launch the graphical interface."""

from __future__ import annotations

import importlib.util

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
    # check the OPTIONAL dependency up front (desktop.py imports webview lazily,
    # so a plain try/except ImportError around the import would never trigger)
    if not browser and importlib.util.find_spec("webview") is None:
        typer.echo(tr("err.no_pywebview"), err=True)
        raise typer.Exit(1)
    from devdash.gui.desktop import run
    run(port=port, browser=browser)
