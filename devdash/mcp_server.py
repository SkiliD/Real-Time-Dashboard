from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__
from .core import config as cfgmod
from .core import db, features, gitwatch, logs, projects, sessions, stats, status as status_mod
from .core import sysopen
from .core.models import MAX_TEXT_LEN  # single source of truth with the GUI API _str cap
from .core import tasks as tsk
from .core import report as report_mod, timeline as tl_mod

PROTOCOL_VERSION = "2024-11-05"

# input bounds for tool arguments (defense in depth on top of the JSON schema)
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_SEARCH_LIMIT = 200
MAX_TITLE_LEN = MAX_TEXT_LEN

# one DB connection per stdio process (the loop is single-threaded)
_CONN = None
_CONN_HOME = None


def _int_arg(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    """Coerce an optional integer argument and clamp it into [lo, hi]."""
    raw = args.get(key)
    if raw is None:
        return default
    n = int(raw)  # ValueError -> clean isError response by the caller
    return max(lo, min(hi, n))


def _tool(name: str, description: str, properties: dict | None = None,
          required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


TOOLS = [
    _tool("devdash_status", "Instant snapshot of the active project: open tasks, running session, today minutes, streak, progress, git."),
    _tool("devdash_projects_list", "List all projects (slug, engine, status, active marker)."),
    _tool("devdash_projects_open", "Open the project's connected folder in the OS file explorer (only the server-stored path from `projects connect`; needs a connected folder).",
          {"project": {"type": "string"}}),
    _tool("devdash_tasks_list", "List tasks with filters.",
          {"project": {"type": "string"}, "status": {"type": "string"},
           "tag": {"type": "string"}, "feature": {"type": "string"},
           "parent": {"type": "string"}}),
    _tool("devdash_tasks_tree", "Task hierarchy: subtasks stacked under their parent, each node with a progress rollup {total, done, pct}.",
          {"project": {"type": "string"}, "status": {"type": "string"}}),
    _tool("devdash_context", "One-call project context bundle for an external AI (project, progress, open task tree, recent logs, git snapshot, today's stats) — read-only.",
          {"project": {"type": "string"}, "open_only": {"type": "boolean"}}),
    _tool("devdash_task_add", "Add a task to the active (or given) project (optionally as a subtask via parent, optionally linked to a feature via feature).",
          {"title": {"type": "string"}, "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
           "tags": {"type": "array", "items": {"type": "string"}},
           "description": {"type": "string"},
           "estimate_min": {"type": "integer"}, "project": {"type": "string"},
           "parent": {"type": "integer"}, "feature": {"type": "integer"}},
          ["title"]),
    _tool("devdash_task_set_status", "Set a task status (todo/in_progress/done/cancelled).",
          {"id": {"type": "integer"}, "status": {"type": "string"}}, ["id", "status"]),
    _tool("devdash_session_start", "Start a work session (optionally on a task).",
          {"task_id": {"type": "integer"}, "kind": {"type": "string", "enum": ["focus", "pause", "custom"]},
           "minutes": {"type": "integer"}, "project": {"type": "string"}}),
    _tool("devdash_session_stop", "Stop the running session with a note.",
          {"note": {"type": "string"}}),
    _tool("devdash_log_add", "Add a devlog entry (decision, blocker, idea).",
          {"content": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, ["content"]),
    _tool("devdash_log_search", "Search devlog entries.",
          {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _tool("devdash_stats", "Stats for a period.", {"period": {"type": "string", "enum": ["today", "week", "month", "streak"]}}, ["period"]),
    _tool("devdash_git_status", "Git snapshot of the active project (branch, last commit, commits today)."),
    _tool("devdash_git_run", "Run a READ-ONLY git command in the linked repo (allowlist: log, diff, status, branch -a...). Write commands are refused.",
          {"cmd": {"type": "string"}}, ["cmd"]),
    _tool("devdash_git_remote", "Remote READ on the linked repo of the active project: fetch, pull --ff-only, remote -v. Remote writes are refused (project rule).",
          {"cmd": {"type": "string"}}, ["cmd"]),
    _tool("devdash_git_log", "Recent commits of the linked repo, optionally filtered by task.",
          {"task_id": {"type": "integer"}, "limit": {"type": "integer"}}),
    _tool("devdash_timeline", "Recent activity feed of a project (event bus: tasks, sessions, devlog, git commits, features...), newest first.",
          {"project": {"type": "string"}, "entity": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("devdash_report", "Markdown week/month report of a project built from LOCAL data (time, tasks, commits, devlog, estimates). ai=true appends the AI week summary when configured.",
          {"project": {"type": "string"}, "period": {"type": "string", "enum": ["week", "month"]}, "ai": {"type": "boolean"}}),
    _tool("devdash_features_list", "List features of the active project with progress."),
    _tool("devdash_ai_ask", "Ask the AI assistant (only when ai_enabled=true in config.json).",
          {"question": {"type": "string"}}, ["question"]),
    _tool("devdash_ai_suggest", "Analyze the active project and propose tickets (READ-ONLY: never creates tasks; creation needs explicit human consent via the GUI/CLI --apply)."),
    _tool("devdash_task_verify", "AI verdict on a task (is its objective met? read-only, never marks done).",
          {"id": {"type": "integer"}}, ["id"]),
]


def _conn(home: Path | None):
    """One cached connection per stdio process (the loop is single-threaded);
    re-opened only if the home changes. Never closed between calls."""
    global _CONN, _CONN_HOME
    h = home or cfgmod.devdash_home()
    if _CONN is None or _CONN_HOME != h:
        _CONN = db.connect(cfgmod.db_path(h))
        db.init_db(_CONN)
        _CONN_HOME = h
    return _CONN


def _proj(conn, args):
    """Active project, overridden by args['project']; KeyError if there is none."""
    proj = projects.get_active(conn)
    if args.get("project"):
        proj = projects.get(conn, args["project"]) or proj
    if proj is None:
        raise KeyError("no active project")
    return proj


def _pid(conn, args):
    """Project id for list endpoints: active project or args['project'] (None allowed)."""
    proj = projects.get_active(conn)
    pid = proj.id if proj else None
    if args.get("project"):
        p = projects.get(conn, args["project"])
        if p is None:
            raise KeyError(f"no project {args['project']!r}")
        pid = p.id
    return pid


def _h_status(conn, args, home):
    return status_mod.snapshot(conn)

def _h_projects_list(conn, args, home):
    active = projects.get_active(conn)
    return {"active": active.slug if active else None,
            "projects": [p.to_dict() for p in projects.list(conn, include_archived=True)]}

def _h_projects_open(conn, args, home):
    proj = _proj(conn, args)
    if not proj.path:
        raise KeyError(f"project {proj.slug} has no connected folder (use 'projects connect <path>')")
    if not Path(proj.path).is_dir():
        raise KeyError(f"project folder no longer exists: {proj.path}")
    sysopen.open_in_explorer(proj.path)
    return {"opened": proj.path, "project": proj.slug}

def _h_tasks_list(conn, args, home):
    pid = _pid(conn, args)
    fid = None
    if args.get("feature"):
        f = features.get(conn, args["feature"], pid)
        if f is None:
            raise KeyError(f"no feature {args['feature']!r}")
        fid = f.id
    parent = args.get("parent")
    if parent is not None:
        parent = "root" if str(parent) == "root" else int(parent)
    rows = tsk.list(conn, project_id=pid,
                    status=[args["status"]] if args.get("status") else None,
                    tag=args.get("tag"), feature_id=fid, parent_id=parent)
    return {"count": len(rows), "tasks": [t.to_dict() for t in rows]}

def _h_tasks_tree(conn, args, home):
    pid = _pid(conn, args)
    roots = tsk.tree(conn, project_id=pid,
                     status=[args["status"]] if args.get("status") else None)
    return {"count": len(roots), "tree": roots}

def _h_context(conn, args, home):
    # one-call context bundle so an external AI (VS Code Copilot, Cline,
    # Vibe...) can "follow" the project without chaining 6 tools
    proj = _proj(conn, args)
    open_only = args.get("open_only", True)
    roots = tsk.tree(conn, project_id=proj.id,
                     status=["todo", "in_progress"] if open_only else None,
                     include_cancelled=not open_only)
    git = gitwatch.status(conn, proj.id)
    recent_commits = [c.to_dict() for c in gitwatch.commits(conn, project_id=proj.id, limit=5)]
    return {
        "project": proj.to_dict(),
        "progress": stats.project_progress(conn, proj.id),
        "today_minutes": round(stats.today_minutes(conn, proj.id), 1),
        "streak_days": stats.streak_days(conn, proj.id),
        "session": (cur.to_dict() if (cur := sessions.current(conn)) else None),
        "tasks": roots,  # nested, with per-node progress rollup
        "recent_logs": [e.to_dict() for e in logs.list(conn, project_id=proj.id, limit=8)],
        "features": features.list(conn, project_id=proj.id),
        "git": {**git, "recent_commits": recent_commits},
    }

def _h_task_add(conn, args, home):
    proj = _proj(conn, args)
    est = args.get("estimate_min")
    if est is not None:
        est = int(est)  # ValueError on garbage -> clean isError
        if est < 0:
            raise ValueError("estimate_min must be >= 0")
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required")
    if len(title) > MAX_TITLE_LEN:
        raise ValueError(f"title too long (max {MAX_TITLE_LEN} chars)")
    desc = args.get("description") or ""
    if len(desc) > MAX_TEXT_LEN:
        raise ValueError(f"description too long (max {MAX_TEXT_LEN} chars)")
    parent = args.get("parent")
    if parent is not None:
        parent = int(parent)  # ValueError on garbage -> clean isError
    feature = args.get("feature")
    if feature is not None:
        feature = int(feature)
    t = tsk.add(conn, proj.id, title, description=desc,
                priority=args.get("priority", "medium"),
                estimate_min=est,
                tags=args.get("tags") or [],
                parent_id=parent, feature_id=feature)
    return t.to_dict()

def _h_task_set_status(conn, args, home):
    return tsk.set_status(conn, int(args["id"]), args["status"]).to_dict()

def _h_session_start(conn, args, home):
    proj = _proj(conn, args)
    minutes = args.get("minutes")
    if minutes is not None:
        minutes = max(1, min(sessions.MAX_MINUTES_PER_DAY, int(minutes)))  # bound: 1 min .. 24 h
    s = sessions.start(conn, proj.id, task_id=args.get("task_id"),
                       kind=args.get("kind", "focus"), minutes=minutes)
    return s.to_dict()

def _h_session_stop(conn, args, home):
    return sessions.stop(conn, None, note=args.get("note", "")).to_dict()

def _h_log_add(conn, args, home):
    proj = projects.get_active(conn)
    e = logs.add(conn, args["content"], project_id=proj.id if proj else None,
                 tags=args.get("tags") or [])
    return e.to_dict()

def _h_log_search(conn, args, home):
    rows = logs.search(conn, args["query"],
                       limit=_int_arg(args, "limit", 20, 1, MAX_SEARCH_LIMIT))
    return {"count": len(rows), "entries": [x.to_dict() for x in rows]}

def _h_stats(conn, args, home):
    proj = projects.get_active(conn)
    pid = proj.id if proj else None
    period = args.get("period", "today")
    if period == "today":
        return {"today_minutes": stats.today_minutes(conn, pid),
                "tasks_done_today": stats.tasks_done_today(conn, pid),
                "streak_days": stats.streak_days(conn, pid)}
    if period == "week":
        return {"minutes_week": stats.period_minutes(conn, pid, 7),
                "per_day": stats.time_by_day(conn, pid, 7), "streak_days": stats.streak_days(conn, pid)}
    if period == "month":
        return {"minutes_month": stats.period_minutes(conn, pid, 30)}
    if period == "streak":
        return {"streak_days": stats.streak_days(conn, pid)}
    raise ValueError(f"unknown period {period!r}")

def _h_git_status(conn, args, home):
    proj = projects.get_active(conn)
    if proj is None:
        raise KeyError("no active project")
    return gitwatch.status(conn, proj.id)

def _h_git_run(conn, args, home):
    proj = projects.get_active(conn)
    repo = gitwatch.get_link(conn, proj.id) if proj else None
    if not repo:
        raise KeyError("no repo linked to the active project")
    return gitwatch.run_readonly(repo, str(args.get("cmd", "")))

def _h_git_remote(conn, args, home):
    proj = projects.get_active(conn)
    repo = gitwatch.get_link(conn, proj.id) if proj else None
    if not repo:
        raise KeyError("no repo linked to the active project")
    return gitwatch.run_remote_read(repo, str(args.get("cmd", "")))

def _h_git_log(conn, args, home):
    proj = projects.get_active(conn)
    task_id = args.get("task_id")
    rows = gitwatch.commits(conn, project_id=proj.id if proj else None,
                            task_id=int(task_id) if task_id is not None else None,
                            limit=_int_arg(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT))
    return {"count": len(rows), "commits": [c.to_dict() for c in rows]}

def _h_timeline(conn, args, home):
    pid = _pid(conn, args)
    entity = args.get("entity") or None
    if entity is not None and entity not in tl_mod.ENTITIES:
        raise KeyError(f"invalid entity {entity!r}, expected one of {list(tl_mod.ENTITIES)}")
    rows = tl_mod.list(conn, project_id=pid, entity=entity,
                       limit=_int_arg(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT))
    return {"count": len(rows), "events": rows}

def _h_report(conn, args, home):
    proj = _proj(conn, args)
    period = str(args.get("period") or "week").lower()
    if period not in report_mod.PERIODS:
        raise KeyError(f"invalid period {period!r}, expected one of {sorted(report_mod.PERIODS)}")
    return report_mod.period_report(conn, proj.id, home=home, period=period,
                                    ai=bool(args.get("ai")))

def _h_features_list(conn, args, home):
    proj = projects.get_active(conn)
    return {"features": features.list(conn, project_id=proj.id if proj else None)}

def _h_ai_ask(conn, args, home):
    from .core.ai import assistant
    from .core.ai.provider import AIDisabledError, AIError
    try:
        return assistant.ask(conn, args["question"], home=home)
    except (AIDisabledError, AIError) as e:
        raise AIError(str(e)) from e

def _h_ai_suggest(conn, args, home):
    from .core.ai import assistant
    from .core.ai.provider import AIDisabledError, AIError
    try:
        # read-only on purpose: an agent NEVER creates tickets by itself
        return assistant.suggest(conn, home=home)
    except (AIDisabledError, AIError) as e:
        raise AIError(str(e)) from e

def _h_task_verify(conn, args, home):
    from .core.ai import assistant
    from .core.ai.provider import AIDisabledError, AIError
    try:
        # read-only on purpose: an agent NEVER marks a task done by itself
        return assistant.task_verdict(conn, int(args["id"]), apply=False, home=home)
    except (AIDisabledError, AIError) as e:
        raise AIError(str(e)) from e

HANDLERS = {
    "devdash_status": _h_status,
    "devdash_projects_list": _h_projects_list,
    "devdash_projects_open": _h_projects_open,
    "devdash_tasks_list": _h_tasks_list,
    "devdash_tasks_tree": _h_tasks_tree,
    "devdash_context": _h_context,
    "devdash_task_add": _h_task_add,
    "devdash_task_set_status": _h_task_set_status,
    "devdash_session_start": _h_session_start,
    "devdash_session_stop": _h_session_stop,
    "devdash_log_add": _h_log_add,
    "devdash_log_search": _h_log_search,
    "devdash_stats": _h_stats,
    "devdash_git_status": _h_git_status,
    "devdash_git_run": _h_git_run,
    "devdash_git_remote": _h_git_remote,
    "devdash_git_log": _h_git_log,
    "devdash_timeline": _h_timeline,
    "devdash_report": _h_report,
    "devdash_features_list": _h_features_list,
    "devdash_ai_ask": _h_ai_ask,
    "devdash_ai_suggest": _h_ai_suggest,
    "devdash_task_verify": _h_task_verify,
}


def _call(name: str, args: dict, home: Path | None) -> dict:
    """Execute one tool via its handler in HANDLERS. Returns a plain dict
    (JSON-serialized by the caller). Unknown tool -> KeyError (clean isError).
    Note: `conn` is cached process-wide on purpose and NOT closed between calls."""
    handler = HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown tool {name!r}")
    conn = _conn(home)
    return handler(conn, args, home)


def handle_request(req, home: Path | None) -> dict | None:
    """One JSON-RPC request -> response dict, or None for notifications.
    `req` may be ANY JSON value (a batch array, a string…): never crash on it."""
    if not isinstance(req, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "invalid request: expected a JSON object"}}
    method = req.get("method", "")
    rid = req.get("id")
    if rid is None:  # notification (e.g. notifications/initialized): no response
        return None
    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "devdash", "version": __version__},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = req.get("params") or {}
            name, args = params.get("name", ""), params.get("arguments") or {}
            try:
                data = _call(name, args, home)
                result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}],
                          "isError": False}
            except Exception as e:
                result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                          "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    except Exception as e:  # protocol-level failure
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32603, "message": f"internal error: {e}"}}


def serve(home: Path | None = None) -> None:
    """Stdio loop: newline-delimited JSON-RPC in, newline-delimited out."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "parse error"}}
        else:
            try:
                resp = handle_request(req, home)
            except Exception as e:  # a malformed request must NEVER kill the server
                resp = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
