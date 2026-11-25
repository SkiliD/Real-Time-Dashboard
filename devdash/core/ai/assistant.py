# AI brains shared by CLI and GUI: context, ask, plan, verify

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .. import config as cfgmod
from .. import features, gitwatch, logs, projects, stats
from .. import i18n
from .. import tasks as tsk
from . import context as ctx_mod
from . import get_provider, ai_status
from .provider import AIDisabledError, AIError

# --- tuning constants (were scattered magic numbers) ----------------------------
ERROR_SNIPPET_LEN = 200      # raw model answer chars shown in a non-JSON AIError
SUMMARY_SESSIONS_LIMIT = 20  # weekly-summary payload: session rows
SUMMARY_LOGS_LIMIT = 20      # weekly-summary payload: devlog rows
SUMMARY_LOG_SNIPPET = 120    # devlog content chars kept in the summary payload
PLAN_COMMITS_LIMIT = 15      # suggest payload: recent commit count
PLAN_COMMIT_MSG_LEN = 120    # suggest payload: commit message chars kept
VERDICT_LINKED_LIMIT = 10    # feature verdict: linked commits listed
VERDICT_MENTION_LEN = 100    # feature verdict: devlog mention chars
TASK_CTX_LIMIT = 20          # task verdict: sessions/devlog rows + SQL LIMIT
TASK_DEVLOG_SNIPPET = 200    # task verdict: devlog content chars kept
SUBTASK_TITLE_MAX = 200      # AI-proposed subtask validation
SUBTASK_EST_MAX_MIN = 10_000
SUBTASK_TAGS_MAX = 10
SUBTASK_TAG_LEN_MAX = 40
TEMPERATURE_CHAT = 0.3       # ask/plan: fairly deterministic
TEMPERATURE_SUGGEST = 0.4    # suggest: a bit more creative
TEMPERATURE_VERDICT = 0.2    # verdicts: as deterministic as possible
VERDICT_EVIDENCE_LIMIT = 10  # verdict: evidence items kept
PROPOSAL_DESC_MAX = 500      # apply: task description chars kept from a proposal

# System prompts live in the i18n catalog (ai.prompt.*) so the model answers in the
# active UI language; they are resolved at CALL time via i18n.tr(...), not frozen
# at import. apply=True (explicit --apply consent) only marks a task done above:
VERDICT_APPLY_MIN_CONFIDENCE = 0.7
MAX_SUGGEST_TASKS = 8      # proposals kept from one analysis
MAX_SUGGEST_APPLY = 20     # hard cap on tickets created in one consented call


def _cfg_and_provider(home: Path | None):
    cfg = cfgmod.load_config(home)
    provider = get_provider(cfg)
    if provider is None:
        raise AIDisabledError(i18n.tr("ai.disabled", home=home))
    return cfg, provider


def _flag(cfg, name: str, home: Path | None = None) -> None:
    if not cfg.ai_feature_flags.get(name, True):
        raise AIDisabledError(i18n.tr("ai.flag_disabled", home=home, name=name))


def _extract_json(text: str, home: Path | None = None) -> dict:
    """Pull the first JSON object out of a model answer. Malformed model output
    must surface as AIError (clean CLI/GUI error), never as a raw JSONDecodeError."""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S) or re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError(i18n.tr("ai.non_json", home=home, snippet=text[:ERROR_SNIPPET_LEN]))
    try:
        return json.loads(m.group(1) if m.groups() else m.group(0))
    except json.JSONDecodeError as e:
        raise AIError(i18n.tr("ai.non_json", home=home, snippet=f"{text[:ERROR_SNIPPET_LEN]} ({e})")) from e


def _normalize_verdict(raw: dict) -> dict:
    """Validate/normalize the model's verdict JSON (never trust it blindly):
    confidence is a float clamped to [0,1], reasoning a str, evidence a short
    list of strings. Shared by task_verdict and feature_verdict so the GUI can
    never render NaN% / >100% / None."""
    conf = raw.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else 0.0
    return {
        "seems_complete": bool(raw.get("seems_complete")),
        "confidence": max(0.0, min(1.0, conf)),
        "reasoning": raw.get("reasoning") if isinstance(raw.get("reasoning"), str) else "",
        "evidence": [str(x) for x in (raw.get("evidence") or []) if isinstance(x, str)][:VERDICT_EVIDENCE_LIMIT],
    }


def _valid_subtask(sub) -> tuple[bool, str]:
    """Validate one model-proposed subtask before it ever touches the DB."""
    if not isinstance(sub, dict):
        return False, i18n.tr("ai.plan.not_object")
    if not isinstance(sub.get("title"), str) or not sub["title"].strip():
        return False, i18n.tr("ai.plan.no_title")
    tags = sub.get("tags")
    if tags is not None and (not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)):
        return False, i18n.tr("ai.plan.bad_tags")
    est = sub.get("estimate_min")
    if est is not None and (isinstance(est, bool) or not isinstance(est, int) or est < 0):
        return False, i18n.tr("ai.plan.bad_est")
    return True, ""


def ctx_logs_search(conn, text: str, project_id: int):
    """Search devlog entries mentioning `text` in a project (verdict context)."""
    from .. import logs
    return logs.search(conn, text, project_id=project_id, limit=5)


def context_preview(conn: sqlite3.Connection, home: Path | None = None) -> dict:
    """Show EXACTLY the compact context that would be sent to the model.

    Purely local (no provider needed, works with AI disabled): lets the user
    audit what a cloud provider would receive before enabling it.
    """
    cfg = cfgmod.load_config(home)
    text = ctx_mod.build_context(conn, max_chars=cfg.ai_context_max_chars)
    return {
        "context": text,
        "chars": len(text),
        "max_chars": cfg.ai_context_max_chars,
        "ai_enabled": cfg.ai_enabled,
        "provider": cfg.ai_provider if cfg.ai_enabled else None,
        "cloud": ai_status(cfg)["cloud"],
    }


def ask(conn: sqlite3.Connection, question: str, home: Path | None = None) -> dict:
    """Free chat with the project context ('où en suis-je ?', 'mes blocages ?')."""
    cfg, provider = _cfg_and_provider(home)
    _flag(cfg, "chat", home)
    sys_prompt = i18n.tr("ai.prompt.ask_ctx") + ctx_mod.build_context(conn, max_chars=cfg.ai_context_max_chars)
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": question},
    ]
    answer = provider.chat(messages)
    return {
        "answer": answer,
        "provider": provider.name,
        "model": cfg.ai_model,
        "cloud": ai_status(cfg)["cloud"],
    }


def summarize_week(conn: sqlite3.Connection, home: Path | None = None) -> dict:
    """Week summary: done, blockers, suggestions."""
    cfg, provider = _cfg_and_provider(home)
    _flag(cfg, "summarize", home)
    active = projects.get_active(conn)
    if active is None:
        raise AIDisabledError(i18n.tr("ai.no_active", home=home))
    time7 = stats.time_by_day(conn, active.id, days=7)
    done7 = stats.tasks_done_by_day(conn, active.id, days=7)
    from .. import logs, sessions
    recent_sessions = sessions.list(conn, project_id=active.id, week=True, limit=SUMMARY_SESSIONS_LIMIT)
    recent_logs = logs.list(conn, project_id=active.id, limit=SUMMARY_LOGS_LIMIT)
    data = {
        "minutes_per_day": time7,
        "tasks_done_per_day": done7,
        "sessions": [{"date": s.started_at[:10], "min": s.duration_min, "note": s.note} for s in recent_sessions],
        "devlog": [e.content[:SUMMARY_LOG_SNIPPET] for e in recent_logs],
    }
    messages = [
        {"role": "system", "content": i18n.tr("ai.prompt.summarize")},
        {"role": "user", "content": i18n.tr("ai.prompt.summarize_user") + json.dumps(data, ensure_ascii=False)},
    ]
    summary = provider.chat(messages)
    return {"summary": summary, "provider": provider.name, "cloud": ai_status(cfg)["cloud"]}


def plan(conn: sqlite3.Connection, title: str, apply: bool = False, home: Path | None = None) -> dict:
    """Split a big task into subtasks. apply=False -> proposal only (NO write).
    apply=True -> creates the subtasks (explicit human consent: CLI --apply)."""
    cfg, provider = _cfg_and_provider(home)
    _flag(cfg, "plan", home)
    messages = [
        {"role": "system", "content": i18n.tr("ai.prompt.plan")},
        {"role": "user", "content": i18n.tr("ai.prompt.plan_ctx") + ctx_mod.build_context(conn, max_chars=cfg.ai_context_max_chars) + "\n\n" + i18n.tr("ai.prompt.plan_user", title=title)},
    ]
    raw = provider.chat(messages, temperature=TEMPERATURE_CHAT)
    raw_subs = _extract_json(raw, home).get("subtasks", [])
    if not isinstance(raw_subs, list):
        raw_subs = []
    proposal, rejected = [], []
    for sub in raw_subs:
        ok, reason = _valid_subtask(sub)
        if ok:
            proposal.append({
                "title": sub["title"].strip(),
                "estimate_min": sub.get("estimate_min"),
                "tags": [t for t in (sub.get("tags") or []) if isinstance(t, str)],
            })
        else:
            rejected.append(reason)
    result = {"title": title, "proposal": proposal, "applied": False, "created_task_ids": [],
              "rejected_subtasks": rejected, "provider": provider.name, "cloud": ai_status(cfg)["cloud"]}
    if apply and proposal:
        active = projects.get_active(conn)
        if active is None:
            raise AIDisabledError(i18n.tr("ai.no_active_apply", home=home))
        for sub in proposal:
            t = tsk.add(conn, active.id, sub["title"],
                        priority="medium", estimate_min=sub.get("estimate_min"),
                        tags=sub.get("tags") or [])
            result["created_task_ids"].append(t.id)
        result["applied"] = True
    return result


def _valid_task_proposal(sub) -> tuple[bool, str]:
    """Validate one AI-proposed ticket before it can ever touch the DB."""
    ok, reason = _valid_subtask(sub)
    if not ok:
        return False, reason
    if len(sub["title"].strip()) > SUBTASK_TITLE_MAX:
        return False, i18n.tr("ai.plan.title_too_long")
    if sub.get("priority", "medium") not in tsk.VALID_PRIORITIES:
        return False, i18n.tr("ai.plan.bad_priority")
    est = sub.get("estimate_min")
    if est is not None and est > SUBTASK_EST_MAX_MIN:
        return False, i18n.tr("ai.plan.est_too_large")
    tags = sub.get("tags") or []
    if len(tags) > SUBTASK_TAGS_MAX or any(not isinstance(t, str) or len(t) > SUBTASK_TAG_LEN_MAX for t in tags):
        return False, i18n.tr("ai.plan.tags_invalid")
    return True, ""


def suggest(conn: sqlite3.Connection, home: Path | None = None) -> dict:
    """Analyze the project and PROPOSE tickets. READ-ONLY: never writes anything.
    Ticket creation happens only via apply_suggestions() = explicit human consent."""
    cfg, provider = _cfg_and_provider(home)
    _flag(cfg, "suggest", home)
    active = projects.get_active(conn)
    if active is None:
        raise AIDisabledError(i18n.tr("ai.no_active", home=home))
    ctx = ctx_mod.build_context(conn, max_chars=cfg.ai_context_max_chars)
    repo = gitwatch.get_link(conn, active.id)
    recent_commits = []
    if repo:
        recent_commits = [c.message[:PLAN_COMMIT_MSG_LEN] for c in gitwatch.commits(conn, repo_path=repo, limit=PLAN_COMMITS_LIMIT)]
    # payload keys are language-neutral identifiers; the model sees the localized prompt
    payload = {"context": ctx, "recent_commits": recent_commits}
    messages = [
        {"role": "system", "content": i18n.tr("ai.prompt.suggest")},
        {"role": "user", "content": i18n.tr("ai.prompt.suggest_user")
                                   + json.dumps(payload, ensure_ascii=False)},
    ]
    raw = provider.chat(messages, temperature=TEMPERATURE_SUGGEST)
    data = _extract_json(raw, home)
    raw_tasks = data.get("tasks", [])
    if not isinstance(raw_tasks, list):
        raw_tasks = []
    proposals, rejected = [], []
    for sub in raw_tasks[:MAX_SUGGEST_TASKS]:
        ok, reason = _valid_task_proposal(sub)
        if ok:
            proposals.append({
                "title": sub["title"].strip(),
                "description": sub.get("description")[:500] if isinstance(sub.get("description"), str) else "",
                "priority": sub.get("priority") or "medium",
                "estimate_min": sub.get("estimate_min"),
                "tags": [t for t in (sub.get("tags") or []) if isinstance(t, str)],
                "reason": sub.get("reason")[:300] if isinstance(sub.get("reason"), str) else "",
            })
        else:
            rejected.append(reason)
    return {"proposals": proposals, "rejected": rejected,
            "provider": provider.name, "cloud": ai_status(cfg)["cloud"]}


def apply_suggestions(conn: sqlite3.Connection, items, home: Path | None = None) -> dict:
    """Create tickets from AI proposals. ONLY called with explicit human consent
    (CLI --apply, or the GUI "create selected" button after the user ticked the
    proposals). Every item is re-validated here before touching the DB; each
    tsk.add appends its event in the same transaction."""
    if not isinstance(items, list) or not items:
        raise AIError("no task to create")
    if len(items) > MAX_SUGGEST_APPLY:
        raise AIError(f"too many tasks at once (max {MAX_SUGGEST_APPLY})")
    cfg = cfgmod.load_config(home)
    _flag(cfg, "suggest", home)
    active = projects.get_active(conn)
    if active is None:
        raise AIDisabledError(i18n.tr("ai.no_active", home=home))
    created = []
    for it in items:
        if not isinstance(it, dict):
            raise AIError("invalid task proposal: not an object")
        ok, reason = _valid_task_proposal(it)
        if not ok:
            raise AIError(f"invalid task proposal: {reason}")
        t = tsk.add(conn, active.id, it["title"].strip(),
                    description=(it.get("description") or "")[:PROPOSAL_DESC_MAX],
                    priority=it.get("priority") or "medium",
                    estimate_min=it.get("estimate_min"),
                    tags=it.get("tags") or [])
        created.append(t.id)
    return {"created": created, "count": len(created)}


def feature_verdict(conn: sqlite3.Connection, ref, home: Path | None = None) -> dict:
    """Cross tasks + commits + devlog to emit a verdict PROPOSAL for a feature.
    NEVER marks done: calls features.propose() at most."""
    cfg, provider = _cfg_and_provider(home)
    _flag(cfg, "feature_verdict", home)
    active = projects.get_active(conn)
    if active is None:
        raise AIDisabledError(i18n.tr("ai.no_active", home=home))
    f = features.get(conn, ref, active.id)
    if f is None:
        raise AIDisabledError(i18n.tr("ai.feature_not_found", home=home, ref=str(ref)))
    sig = features.check_completion(conn, f.id)
    repo = gitwatch.get_link(conn, active.id)
    linked = []
    if repo:
        linked = [c.message for c in gitwatch.commits(conn, repo_path=repo)
                  if c.parsed_feature and c.parsed_feature.lower() == f.name.lower()][:VERDICT_LINKED_LIMIT]
    mentions = [e.content[:VERDICT_MENTION_LEN] for e in ctx_logs_search(conn, f.name, active.id)]
    payload = {
        "feature": f.name,
        "tasks_total": sig["total"], "tasks_done": sig["done"], "all_tasks_done": sig["all_done"],
        "linked_commits": linked, "devlog_mentions": mentions,
    }
    messages = [
        {"role": "system", "content": i18n.tr("ai.prompt.verdict")},
        {"role": "user", "content": i18n.tr("ai.prompt.verdict_user") + json.dumps(payload, ensure_ascii=False)},
    ]
    raw = provider.chat(messages, temperature=TEMPERATURE_VERDICT)
    verdict = _normalize_verdict(_extract_json(raw, home))
    proposed = False
    if verdict["seems_complete"] and f.status not in ("done", "rejected"):
        f = features.propose(conn, f.id, source="ai",
                             confidence=verdict["confidence"],
                             reasoning=verdict["reasoning"] or None)
        proposed = True
    return {"verdict": verdict, "feature": f.to_dict(), "proposed": proposed,
            "provider": provider.name, "cloud": ai_status(cfg)["cloud"]}


def task_verdict(conn: sqlite3.Connection, task_id: int, apply: bool = False,
                 home: Path | None = None) -> dict:
    """Cross task + subtasks + linked commits + devlog + work sessions to emit a
    verdict on whether a TASK's objective is met.

    SAFETY: read-only by default. apply=True is the explicit human consent
    (CLI --apply only — the GUI never sends it; its approve button performs the
    status change itself): the task is marked done ONLY if the model says
    seems_complete with confidence >= VERDICT_APPLY_MIN_CONFIDENCE.
    """
    cfg, provider = _cfg_and_provider(home)
    _flag(cfg, "task_verdict", home)
    t = tsk.get(conn, task_id)
    if t is None:
        raise KeyError(i18n.tr("err.no_task", id=task_id))
    children = [x for x in tsk.list(conn, project_id=t.project_id) if x.parent_id == t.id]
    repo = gitwatch.get_link(conn, t.project_id)
    commits = []
    if repo:
        commits = [{"short": c.short, "message": c.message, "at": c.committed_at}
                   for c in gitwatch.commits(conn, repo_path=repo, task_id=t.id, limit=TASK_CTX_LIMIT)]
    devlog = [{"at": e.created_at, "content": e.content[:TASK_DEVLOG_SNIPPET]}
              for e in logs.list(conn, task_id=t.id, limit=TASK_CTX_LIMIT)]
    sess_rows = conn.execute(
        "SELECT kind, started_at, duration_min, note FROM sessions "
        f"WHERE task_id = ? ORDER BY started_at DESC LIMIT {TASK_CTX_LIMIT}", (t.id,),
    ).fetchall()
    proj = projects.get(conn, t.project_id)
    payload = {
        "project": proj.slug if proj else None,
        "task": t.to_dict(),
        "subtasks": [{"id": s.id, "title": s.title, "status": s.status} for s in children],
        "linked_commits": commits,
        "devlog_entries": devlog,
        "work_sessions": [{"kind": r["kind"], "minutes": r["duration_min"], "note": r["note"]}
                          for r in sess_rows],
    }
    messages = [
        {"role": "system", "content": i18n.tr("ai.prompt.task_verdict")},
        {"role": "user", "content": i18n.tr("ai.prompt.task_verdict_user") + json.dumps(payload, ensure_ascii=False, default=str)},
    ]
    raw = provider.chat(messages, temperature=TEMPERATURE_VERDICT)
    verdict = _normalize_verdict(_extract_json(raw, home))
    applied = False
    if apply and verdict["seems_complete"] and verdict["confidence"] >= VERDICT_APPLY_MIN_CONFIDENCE \
            and t.status != "done":
        t = tsk.set_status(conn, t.id, "done")  # explicit consent was given via apply=True
        applied = True
    return {"verdict": verdict, "task": t.to_dict(), "applied": applied,
            "provider": provider.name, "cloud": ai_status(cfg)["cloud"]}
