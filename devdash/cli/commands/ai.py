"""`devdash ai ...` commands — OPTIONAL AI assistant.

Without AI configured (ai_enabled=false in ~/.devdash/config.json), these
commands fail cleanly with a clear message and the rest of DevDash is fully
functional. No AI action ever writes without explicit consent (--apply).
"""

from __future__ import annotations

import typer
from rich.console import Console

from devdash.core import config as cfgmod
from devdash.core.ai import assistant, ai_status
from devdash.core.ai.provider import AIDisabledError, AIError
from devdash.core.i18n import tr
from ..deps import get_conn
from ..output import emit

app = typer.Typer(help=tr("group.ai.help"))
console = Console()


def _cloud_warning(cfg) -> None:
    st = ai_status(cfg)
    if st["cloud"]:
        console.print(tr("ai.cloud_warning", provider=st["provider"]))


def _run(ctx, fn, *args, **kwargs):
    conn = get_conn(ctx)
    try:
        return fn(conn, *args, home=cfgmod.devdash_home(), **kwargs)
    except (AIDisabledError, AIError) as e:
        # a clean runtime error, not a usage error: message + exit 1 (not BadParameter/exit 2)
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


@app.command("context", help=tr("ai.context.help"))
def context_cmd(ctx: typer.Context) -> None:
    """Show the compact project context that WOULD be sent to the AI.

    Purely local: nothing is sent anywhere, even with the AI enabled."""
    from devdash.core.ai import context as ctx_mod
    conn = get_conn(ctx)
    r = assistant.context_preview(conn, home=cfgmod.devdash_home())
    emit(ctx, r, lambda: (
        console.print(tr("ai.context.line", chars=r["chars"], max=r["max_chars"],
                         provider=r["provider"] or "-", cloud=r["cloud"])),
        console.print(r["context"]),
    ))


@app.command("ask", help=tr("ai.ask.help"))
def ask_cmd(
    ctx: typer.Context,
    question: str = typer.Argument(..., help=tr("opt.question")),
) -> None:
    """Ask the AI assistant (knows the compact project context)."""
    r = _run(ctx, assistant.ask, question)
    _cloud_warning(cfgmod.load_config())
    emit(ctx, r, lambda: console.print(r["answer"]))


@app.command("summarize", help=tr("ai.summarize.help"))
def summarize_cmd(
    ctx: typer.Context,
    period: str = typer.Option("week", "--period", "-p", help=tr("opt.period")),
) -> None:
    """AI summary of the week: done, blockers, suggestions."""
    if period != "week":
        raise typer.BadParameter(tr("err.period"))
    r = _run(ctx, assistant.summarize_week)
    _cloud_warning(cfgmod.load_config())
    emit(ctx, r, lambda: console.print(r["summary"]))


@app.command("plan", help=tr("ai.plan.help"))
def plan_cmd(
    ctx: typer.Context,
    title: str = typer.Argument(..., help=tr("opt.big_task")),
    apply: bool = typer.Option(False, "--apply", help=tr("opt.apply")),
) -> None:
    """Split a big task into subtasks. --apply creates them (explicit consent)."""
    r = _run(ctx, assistant.plan, title, apply=apply)
    _cloud_warning(cfgmod.load_config())

    def render() -> None:
        console.print(tr("ai.plan.title", n=len(r["proposal"])))
        for i, s in enumerate(r["proposal"], 1):
            est = tr("ai.plan.est", n=s["estimate_min"]) if s.get("estimate_min") else ""
            tags = tr("ai.plan.tags", tags=",".join(s.get("tags") or [])) if s.get("tags") else ""
            console.print(tr("ai.plan.item", i=i, title=s.get("title"), est=est, tags=tags))
        if r["applied"]:
            console.print(tr("ai.plan.applied", n=len(r["created_task_ids"]),
                             ids=", ".join(f"#{i}" for i in r["created_task_ids"])))
        else:
            console.print(tr("ai.plan.not_applied"))

    emit(ctx, r, render)


@app.command("verdict", help=tr("ai.verdict.help"))
def verdict_cmd(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=tr("opt.ref_feat")),
) -> None:
    """AI verdict on a feature (tasks + commits + devlog). Proposes, NEVER marks done."""
    r = _run(ctx, assistant.feature_verdict, ref)
    _cloud_warning(cfgmod.load_config())

    def render() -> None:
        v = r["verdict"]
        verdict = tr("verdict.complete") if v.get("seems_complete") else tr("verdict.incomplete")
        console.print(tr("ai.verdict.line", name=r["feature"]["name"], verdict=verdict,
                         conf=v.get("confidence")))
        console.print(tr("ai.verdict.reasoning", text=v.get("reasoning")))
        if r["proposed"]:
            console.print(tr("ai.verdict.proposed", name=r["feature"]["name"]))
        else:
            console.print(tr("ai.verdict.none", status=r["feature"]["status"]))

    emit(ctx, r, render)
