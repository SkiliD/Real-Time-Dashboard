"""Minimal MCP (Model Context Protocol) server for DevDash — stdlib only.

Speaks JSON-RPC 2.0 over stdio, newline-delimited (the MCP stdio transport):
each line on stdin is one request/notification, each response is one line on
stdout. Logs go to stderr only, never stdout (stdout is the protocol channel).

Run with:  devdash mcp
Configure in Claude Code (project `.mcp.json`):
    { "mcpServers": { "devdash": { "command": "devdash", "args": ["mcp"] } } }
Configure in Vibe (`~/.vibe/config.toml` or project `.vibe/`):
    [mcp_servers.devdash]
    command = "devdash"
    args = ["mcp"]

Safety rules (same as the rest of DevDash): read-only git, no destructive
action exposed, AI tools only when ai_enabled=true, and a feature is NEVER
marked done by the agent — only proposed/approved by a human in DevDash.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__
from .core import config as cfgmod
from .core import db, events, features, gitwatch, logs, projects, sessions, stats, status as status_mod
from .core import tasks as tsk

PROTOCOL_VERSION = "2024-11-05"

# input bounds for tool arguments (defense in depth on top of the JSON schema)
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_SEARCH_LIMIT = 200

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
    _tool("devdash_tasks_list", "List tasks with filters.",
          {"project": {"type": "string"}, "status": {"type": "string"},
           "tag": {"type": "string"}, "feature": {"type": "string"}}),
    _tool("devdash_task_add", "Add a task to the active (or given) project.",
          {"title": {"type": "string"}, "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
           "tags": {"type": "array", "items": {"type": "string"}},
           "estimate_min": {"type": "integer"}, "project": {"type": "string"}},
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
    _tool("devdash_git_log", "Recent commits of the linked repo, optionally filtered by task.",
          {"task_id": {"type": "integer"}, "limit": {"type": "integer"}}),
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


def _call(name: str, args: dict, home: Path | None) -> dict:
    """Execute one tool. Returns a plain dict (JSON-serialized by the caller)."""
    conn = _conn(home)
    if name == "devdash_status":
        return status_mod.snapshot(conn)
    if name == "devdash_projects_list":
        active = projects.get_active(conn)
        return {"active": active.slug if active else None,
                "projects": [p.to_dict() for p in projects.list(conn, include_archived=True)]}
    if name == "devdash_tasks_list":
        proj = projects.get_active(conn)
        pid = proj.id if proj else None
        if args.get("project"):
            p = projects.get(conn, args["project"])
            if p is None:
                raise KeyError(f"no project {args['project']!r}")
            pid = p.id
        fid = None
        if args.get("feature"):
            f = features.get(conn, args["feature"], pid)
            if f is None:
                raise KeyError(f"no feature {args['feature']!r}")
            fid = f.id
        rows = tsk.list(conn, project_id=pid,
                        status=[args["status"]] if args.get("status") else None,
                        tag=args.get("tag"), feature_id=fid)
        return {"count": len(rows), "tasks": [t.to_dict() for t in rows]}
    if name == "devdash_task_add":
        proj = projects.get_active(conn)
        if args.get("project"):
            proj = projects.get(conn, args["project"]) or proj
        if proj is None:
            raise KeyError("no active project")
        est = args.get("estimate_min")
        if est is not None:
            est = int(est)  # ValueError on garbage -> clean isError
            if est < 0:
                raise ValueError("estimate_min must be >= 0")
        t = tsk.add(conn, proj.id, args["title"],
                    priority=args.get("priority", "medium"),
                    estimate_min=est,
                    tags=args.get("tags") or [])
        return t.to_dict()
    if name == "devdash_task_set_status":
        return tsk.set_status(conn, int(args["id"]), args["status"]).to_dict()
    if name == "devdash_session_start":
        proj = projects.get_active(conn)
        if args.get("project"):
            proj = projects.get(conn, args["project"]) or proj
        if proj is None:
            raise KeyError("no active project")
        minutes = args.get("minutes")
        if minutes is not None:
            minutes = max(1, min(24 * 60, int(minutes)))  # bound: 1 min .. 24 h
        s = sessions.start(conn, proj.id, task_id=args.get("task_id"),
                           kind=args.get("kind", "focus"), minutes=minutes)
        return s.to_dict()
    if name == "devdash_session_stop":
        return sessions.stop(conn, None, note=args.get("note", "")).to_dict()
    if name == "devdash_log_add":
        proj = projects.get_active(conn)
        e = logs.add(conn, args["content"], project_id=proj.id if proj else None,
                     tags=args.get("tags") or [])
        return e.to_dict()
    if name == "devdash_log_search":
        rows = logs.search(conn, args["query"],
                           limit=_int_arg(args, "limit", 20, 1, MAX_SEARCH_LIMIT))
        return {"count": len(rows), "entries": [x.to_dict() for x in rows]}
    if name == "devdash_stats":
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
    if name == "devdash_git_status":
        proj = projects.get_active(conn)
        if proj is None:
            raise KeyError("no active project")
        return gitwatch.status(conn, proj.id)
    if name == "devdash_git_log":
        proj = projects.get_active(conn)
        task_id = args.get("task_id")
        rows = gitwatch.commits(conn, project_id=proj.id if proj else None,
                                task_id=int(task_id) if task_id is not None else None,
                                limit=_int_arg(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT))
        return {"count": len(rows), "commits": [c.to_dict() for c in rows]}
    if name == "devdash_features_list":
        proj = projects.get_active(conn)
        return {"features": features.list(conn, project_id=proj.id if proj else None)}
    if name == "devdash_ai_ask":
        from .core.ai import assistant
        from .core.ai.provider import AIDisabledError, AIError
        try:
            return assistant.ask(conn, args["question"], home=home)
        except (AIDisabledError, AIError) as e:
            raise AIError(str(e)) from e
    if name == "devdash_ai_suggest":
        from .core.ai import assistant
        from .core.ai.provider import AIDisabledError, AIError
        try:
            # read-only on purpose: an agent NEVER creates tickets by itself
            return assistant.suggest(conn, home=home)
        except (AIDisabledError, AIError) as e:
            raise AIError(str(e)) from e
    if name == "devdash_task_verify":
        from .core.ai import assistant
        from .core.ai.provider import AIDisabledError, AIError
        try:
            # read-only on purpose: an agent NEVER marks a task done by itself
            return assistant.task_verdict(conn, int(args["id"]), apply=False, home=home)
        except (AIDisabledError, AIError) as e:
            raise AIError(str(e)) from e
    raise KeyError(f"unknown tool {name!r}")
    # note: `conn` is cached process-wide on purpose and NOT closed between calls


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
