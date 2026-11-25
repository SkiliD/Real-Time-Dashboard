"""build the small context blob we send to the model"""

from __future__ import annotations

import sqlite3

from .. import features, gitwatch, logs, projects, sessions, stats
from .. import tasks as tsk
from ..i18n import tr

DEFAULT_MAX_CHARS = 6000

# per-section caps so one verbose section cannot crowd out the others
OPEN_TASKS_LIMIT = 12
TREE_ROOTS_LIMIT = 8
TREE_LINES_LIMIT = 20
RECENT_LOGS_LIMIT = 5
LOG_SNIPPET_LEN = 80
RECENT_COMMITS_LIMIT = 5
COMMIT_MSG_LEN = 50
GIT_MSG_LEN = 60

# prompt-injection defense: everything below comes from the user's own data
# (task titles, devlog, COMMIT MESSAGES from a possibly hostile clone...)
# — delimit it loudly so the model treats it as data, never as instructions.
# The markers themselves are localized (ai.ctx.begin / ai.ctx.end).


def _ctx_header(active, prog: dict) -> str:
    langs = ", ".join(active.languages) if active.languages else tr("ai.ctx.langs_none")
    return tr("ai.ctx.header", name=active.name, engine=active.engine, langs=langs,
              status=active.status, pct=prog["pct_weighted"], done=prog["done"], total=prog["total"])


def _ctx_open_tasks(conn: sqlite3.Connection, project_id: int) -> str:
    open_tasks = tsk.list(conn, project_id=project_id, status=["todo", "in_progress"],
                          include_cancelled=False)
    if open_tasks:
        listing = "; ".join(
            f"#{t.id} {t.title} [{t.priority}/{t.status}]" for t in open_tasks[:OPEN_TASKS_LIMIT]
        )
        return tr("ai.ctx.tasks", items=listing)
    return tr("ai.ctx.tasks_none")


def _fmt_node(n: dict, depth: int = 0) -> list[str]:
    p = n.get("progress") or {}
    subs = tr("ai.ctx.subs_done", done=p["done"], total=p["total"]) if p.get("total") else ""
    head = f"{'  ' * depth}#{n['id']} {n['title']} [{n['status']}]{subs}"
    return [head] + [x for k in n.get("children", []) for x in _fmt_node(k, depth + 1)]


def _ctx_tree(conn: sqlite3.Connection, project_id: int) -> str | None:
    # subtask hierarchy (Notion-style stacking) so the model sees the structure,
    # not just a flat list: roots with their progress rollup + direct children
    roots = tsk.tree(conn, project_id=project_id, status=["todo", "in_progress"])
    if not roots:
        return None
    tree_lines = [x for r in roots[:TREE_ROOTS_LIMIT] for x in _fmt_node(r)]
    if not tree_lines:
        return None
    return tr("ai.ctx.tree", lines="\n".join(tree_lines[:TREE_LINES_LIMIT]))


def _ctx_session(conn: sqlite3.Connection, project_id: int) -> str | None:
    s = sessions.current(conn)
    if s is not None and s.project_id == project_id:
        extra = tr("ai.ctx.session_task", id=s.task_id) if s.task_id else ""
        return tr("ai.ctx.session", id=s.id, kind=s.kind,
                  mins=f"{sessions.elapsed_min(s):.0f}") + extra
    return None


def _ctx_stats(conn: sqlite3.Connection, project_id: int) -> str:
    return tr("ai.ctx.stats",
              today=f"{stats.today_minutes(conn, project_id):.0f}",
              streak=stats.streak_days(conn, project_id),
              done7=sum(d["count"] for d in stats.tasks_done_by_day(conn, project_id, 7)))


def _ctx_logs(conn: sqlite3.Connection, project_id: int) -> str | None:
    recent_logs = logs.list(conn, project_id=project_id, limit=RECENT_LOGS_LIMIT)
    if recent_logs:
        return tr("ai.ctx.logs", items=" | ".join(e.content[:LOG_SNIPPET_LEN] for e in recent_logs))
    return None


def _ctx_features(conn: sqlite3.Connection, project_id: int) -> str | None:
    feats_open = features.list(conn, project_id=project_id, status="open")
    feats_proposed = features.list(conn, project_id=project_id, status="proposed")
    if feats_open or feats_proposed:
        listing = "; ".join(
            f"{f['name']} ({f['status']}, {f['progress']['done']}/{f['progress']['total']})"
            for f in feats_open + feats_proposed
        )
        return tr("ai.ctx.features", items=listing)
    return None


def _ctx_git(conn: sqlite3.Connection, project_id: int, repo: str) -> list[str]:
    lines = []
    st = gitwatch.status(conn, project_id)
    last = st.get("last_commit") or {}
    if last:
        lines.append(tr("ai.ctx.git_branch", branch=st.get("branch"), short=last.get("short"),
                        msg=str(last.get("message"))[:GIT_MSG_LEN]))
    # last commits from the cache (read-only) so the model sees the recent work
    rows = conn.execute(
        "SELECT short, message, committed_at, parsed_task_id FROM git_commits "
        "WHERE repo_path = ? ORDER BY committed_at DESC LIMIT ?",
        (repo, RECENT_COMMITS_LIMIT),
    ).fetchall()
    if rows:
        lines.append(tr("ai.ctx.commits", items=" | ".join(
            f"{r['short']} « {str(r['message'])[:COMMIT_MSG_LEN]} »"
            + (tr("ai.ctx.commit_task", id=r["parsed_task_id"]) if r["parsed_task_id"] else "")
            for r in rows
        )))
    return lines


def build_context(conn: sqlite3.Connection, project_id: int | None = None,
                  max_chars: int = DEFAULT_MAX_CHARS) -> str:
    active = projects.get(conn, project_id) if project_id else projects.get_active(conn)
    if active is None:
        return tr("ai.ctx.none")
    prog = stats.project_progress(conn, active.id)
    sections = [
        _ctx_header(active, prog),
        _ctx_open_tasks(conn, active.id),
        _ctx_tree(conn, active.id),
        _ctx_session(conn, active.id),
        _ctx_stats(conn, active.id),
        _ctx_logs(conn, active.id),
        _ctx_features(conn, active.id),
    ]
    repo = gitwatch.get_link(conn, active.id)
    if repo:
        sections += _ctx_git(conn, active.id, repo)
    text = "\n".join(s for s in sections if s)
    text = tr("ai.ctx.begin") + text + tr("ai.ctx.end")
    if len(text) > max_chars:
        text = text[:max_chars] + tr("ai.ctx.truncated")
    return text
