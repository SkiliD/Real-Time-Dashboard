# devdash status -- the one-glance snapshot
# Works on Windows, Mac and Linux.

from __future__ import annotations

import json
import time

import typer
from rich.console import Console

from devdash.core import status as core_status
from devdash.core.i18n import tr
from ..deps import get_conn, resolve_project
from ..output import emit

console = Console()


def _render(data: dict) -> None:
    p = data.get("project")
    if not p:
        console.print(tr("status.no_project"))
        return
    tasks = data["tasks"]
    sess = data.get("session")
    git = data.get("git") or {}
    prog = data.get("progress") or {}
    inprog = tasks["in_progress"]
    console.print(
        tr("status.line1", color=p["color"], name=p["name"], engine=p["engine"], status=p["status"],
           pct=prog.get("pct_weighted", 0), done=prog.get("done", 0), total=prog.get("total", 0))
    )
    line_tasks = tr("status.tasks", open=tasks["open"], done_today=tasks["done_today"])
    if inprog:
        t = inprog[0]
        line_tasks += tr("status.tasks_inprog", id=t["id"], title=t["title"])
    console.print("  " + line_tasks)
    if sess:
        console.print(
            tr("status.session_running", id=sess["id"], kind=sess["kind"],
               elapsed=f"{sess['elapsed_min']:.1f}",
               task=tr("status.session_task", id=sess["task_id"]) if sess.get("task_id") else "")
        )
    else:
        console.print(tr("status.session_none"))
    console.print(
        tr("status.time", today=f"{data['today_minutes']:.0f}", streak=data["streak_days"])
    )
    if git.get("linked"):
        last = git.get("last_commit") or {}
        console.print(
            tr("status.git_line", branch=git.get("branch"), n=git.get("commits_today"),
               last=(tr("status.git_last", short=last.get("short"), msg=str(last.get("message"))[:50])
                     if last else ""))
        )
    else:
        console.print(tr("status.git_none"))


def status_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help=tr("opt.project_default")),
    watch: bool = typer.Option(False, "--watch", "-w", help=tr("opt.watch")),
    interval: float = typer.Option(2.0, "--interval", "-i", help=tr("opt.interval")),
) -> None:
    """Instant snapshot of the active project (tasks, session, time, streak, git)."""
    conn = get_conn(ctx)
    pid = resolve_project(conn, project).id if project else None
    json_mode = bool(ctx.obj and ctx.obj.get("json"))

    if not watch:
        data = core_status.snapshot(conn, pid)
        emit(ctx, data, lambda: _render(data))
        return

    try:
        while True:
            data = core_status.snapshot(conn, pid)
            if json_mode:
                typer.echo(json.dumps(data, ensure_ascii=False, default=str))
            else:
                console.clear()
                _render(data)
                console.print(tr("status.watch_line", ts=data["generated_at"]))
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
