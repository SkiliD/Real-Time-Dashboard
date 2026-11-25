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


def fmt_ts(iso: str | None) -> str:
    """'2026-11-02T14:03:22' -> '2026-11-02 14:03' ('' for None)."""
    return (iso or "")[:16].replace("T", " ")


def fmt_date(iso: str | None) -> str:
    """'2026-11-02T14:03:22' -> '2026-11-02' ('' for None)."""
    return (iso or "")[:10]


def fmt_time(iso: str | None) -> str:
    """'2026-11-02T14:03:22' -> '14:03:22' ('' for None)."""
    return (iso or "")[11:19]
