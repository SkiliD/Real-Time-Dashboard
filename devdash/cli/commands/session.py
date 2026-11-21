"""`devdash session ...` commands: work sessions + Pomodoro timer.

- `session start` creates the session AND runs an interactive live timer
  (Ctrl+C to stop, then a note is prompted). Use --background for scripts.
- `session stop --note "..."` stops the running session.
"""

from __future__ import annotations

import time

import typer
from rich.console import Console

from devdash.core import config as cfgmod
from devdash.core import sessions as core
from devdash.core.i18n import tr, tr_cols
from ..deps import get_conn, guard, resolve_project
from ..output import emit

app = typer.Typer(help=tr("group.session.help"))
console = Console()


def _fmt_hms(total_seconds: float) -> str:
    s = max(0, int(total_seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _maybe_start_break(conn, cfg, stopped, confirm=None) -> bool:
    """After a pomodoro FOCUS session, offer to start the break automatically.
    Returns True if a pause session was started. `confirm` is injectable for tests."""
    if confirm is None:
        confirm = typer.confirm  # resolved at call time so tests can monkeypatch
    if stopped.kind != "focus":
        return False
    if not confirm(tr("timer.break_prompt", brk=cfg.pomodoro_break_min), default=True):
        return False
    brk = core.start(conn, stopped.project_id, task_id=stopped.task_id,
                     kind="pause", minutes=cfg.pomodoro_break_min)
    console.print(tr("timer.break_started", id=brk.id, brk=cfg.pomodoro_break_min))
    return True


def _interactive_timer(conn, pomodoro: bool) -> None:
    """Blocking live timer in the terminal until Ctrl+C, then stop + note."""
    cfg = cfgmod.load_config()
    print()
    try:
        while True:
            s = core.current(conn)
            if s is None:
                break
            elapsed = core.elapsed_min(s) * 60
            line = tr("timer.line", id=s.id, kind=s.kind, elapsed=_fmt_hms(elapsed))
            if s.planned_min:
                remaining = max(0.0, s.planned_min * 60 - elapsed)
                line += tr("timer.remaining", remaining=_fmt_hms(remaining))
            print("\r" + line + tr("timer.ctrlc"), end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        note = typer.prompt(tr("timer.prompt"), default="", show_default=False)
        stopped = core.stop(conn, note=note)
        console.print(
            tr("timer.stopped", id=stopped.id, dur=stopped.duration_min)
        )
        if pomodoro and stopped.kind == "focus":
            if _maybe_start_break(conn, cfg, stopped):
                _interactive_timer(conn, pomodoro=False)  # run the break timer (no loop)


@app.command("start", help=tr("session.start.help"))
def start_cmd(
    ctx: typer.Context,
    task: int | None = typer.Option(None, "--task", "-t", help=tr("opt.task_session")),
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    kind: str = typer.Option("focus", "--kind", help=tr("opt.kind", list=", ".join(core.VALID_KINDS))),
    minutes: int | None = typer.Option(None, "--minutes", "-m", help=tr("opt.minutes")),
    background: bool = typer.Option(False, "--background", "-b", help=tr("opt.background")),
    pomodoro: bool = typer.Option(False, "--pomodoro", help=tr("opt.pomodoro")),
) -> None:
    """Start a work session (interactive timer unless --background)."""
    conn = get_conn(ctx)
    proj = resolve_project(conn, project)
    session = core.start(conn, proj.id, task_id=task, kind=kind, minutes=minutes)
    data = {**session.to_dict(), "project": proj.slug}

    def render() -> None:
        task_suffix = tr("session.task_suffix", task=task) if task else ""
        target_suffix = tr("session.target_suffix", minutes=minutes) if minutes else ""
        console.print(tr("session.started", id=session.id, slug=proj.slug,
                         task_suffix=task_suffix, target_suffix=target_suffix))

    emit(ctx, data, render)
    if not background and not (ctx.obj and ctx.obj.get("json")):
        _interactive_timer(conn, pomodoro)


@app.command("stop", help=tr("session.stop.help"))
def stop_cmd(
    ctx: typer.Context,
    note: str = typer.Option("", "--note", "-n", help=tr("opt.note")),
    id: int | None = typer.Option(None, "--id", help=tr("opt.id")),
) -> None:
    """Stop a session and record its duration + note."""
    conn = get_conn(ctx)
    session = guard(core.stop, conn, id, note=note)  # unknown id / no running session / already stopped
    emit(ctx, session.to_dict(), lambda: console.print(
        tr("session.stopped", id=session.id, dur=session.duration_min,
           note_suffix=tr("session.note_suffix", note=note) if note else "")
    ))


@app.command("now", help=tr("session.now.help"))
def now_cmd(ctx: typer.Context) -> None:
    """Show the currently running session (if any) with live elapsed time."""
    conn = get_conn(ctx)
    s = core.current(conn)
    if s is None:
        emit(ctx, {"running": False, "session": None}, lambda: console.print(tr("session.none")))
        return
    data = {"running": True, "session": s.to_dict(), "elapsed_min": core.elapsed_min(s)}

    def render() -> None:
        console.print(
            tr("session.now_line", id=s.id, kind=s.kind, elapsed=f"{core.elapsed_min(s):.1f}",
               task_suffix=tr("session.task_suffix", task=s.task_id) if s.task_id else "")
        )

    emit(ctx, data, render)


@app.command("list", help=tr("session.list.help"))
def list_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    today: bool = typer.Option(False, "--today"),
    week: bool = typer.Option(False, "--week"),
    task: int | None = typer.Option(None, "--task", "-t"),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """Session history (newest first)."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    rows = core.list(conn, project_id=pid, task_id=task, today=today, week=week, limit=limit)
    data = {"count": len(rows), "sessions": [r.to_dict() for r in rows]}

    def render() -> None:
        from rich.table import Table
        table = Table(title=tr("session.table.title"))
        for col in tr_cols("session.cols"):
            table.add_column(col)
        for r in rows:
            dur = f"{r.duration_min}m" if r.duration_min is not None else tr("session.running")
            table.add_row(str(r.id), r.kind, dur, r.started_at[11:19] + " " + r.started_at[:10],
                          str(r.project_id), f"#{r.task_id}" if r.task_id else "", r.note[:40])
        console.print(table)

    emit(ctx, data, render)
