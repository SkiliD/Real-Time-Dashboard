"""rich by default, --json when scripting"""

from __future__ import annotations

import json

import typer


def emit(ctx: typer.Context, data, render) -> None:
    """Print JSON if --json was passed globally, else call the rich renderer."""
    if ctx.obj and ctx.obj.get("json"):
        typer.echo(json.dumps(data, ensure_ascii=False, default=str))
    else:
        render()
