"""AI assistant business functions (step i). Used by BOTH the CLI (`devdash ai
...`) and the GUI (chat panel). SAFETY RULES:
- read-only by default: `plan(apply=False)` only proposes, never writes;
  `apply=True` (CLI --apply / GUI confirm button) is the explicit human consent;
- `feature_verdict` NEVER marks a feature done — it only records a proposal
  (features.propose) that a human must approve/reject.
- context sent to the model is built by context.build_context (compact summary).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .. import config as cfgmod
from .. import features, gitwatch, projects, stats
from .. import tasks as tsk
from . import context as ctx_mod
from . import get_provider, ai_status
from .provider import AIDisabledError, AIError

PLAN_SYSTEM = (
    "Tu es un assistant de planification pour un développeur solo. "
    "Réponds UNIQUEMENT un JSON valide: "
    '{"subtasks": [{"title": str, "estimate_min": int|null, "tags": [str]}]} '
    "3 à 6 sous-tâches concrètes, en français."
)
VERDICT_SYSTEM = (
    "Tu es un assistant qui évalue si une feature est complète. "
    "Réponds UNIQUEMENT un JSON valide: "
    '{"seems_complete": bool, "confidence": 0..1, "reasoning": str en français}. '
    "Ne marque JAMAIS la feature comme terminée toi-même: tu donnes juste un verdict."
)


def _cfg_and_provider(home: Path | None):
    cfg = cfgmod.load_config(home)
    provider = get_provider(cfg)
    if provider is None:
        raise AIDisabledError(
            "IA désactivée. Activez-la dans ~/.devdash/config.json "
            '(ai_enabled=true, ai_provider="ollama"|"mistral"|"openai"|"mock", ai_model=..., ai_api_key=... pour le cloud)'
        )
    return cfg, provider


def _flag(cfg, name: str) -> None:
    if not cfg.ai_feature_flags.get(name, True):
        raise AIDisabledError(f"fonction IA '{name}' désactivée dans config.json (ai_feature_flags)")


def _extract_json(text: str) -> dict:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S) or re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError(f"réponse IA non JSON: {text[:200]}")
    return json.loads(m.group(1) if m.groups() else m.group(0))


def ask(conn: sqlite3.Connection, question: str, home: Path | None = None) -> dict:
    """Free chat with the project context ('où en suis-je ?', 'mes blocages ?')."""
    cfg, provider = _cfg_and_provider(home)
    _flag(cfg, "chat")
    sys_prompt = "Contexte projet (résumé compact, ne le cite pas tel quel):\n" + ctx_mod.build_context(conn)
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
    _flag(cfg, "summarize")
    active = projects.get_active(conn)
    if active is None:
        raise AIDisabledError("aucun projet actif")
    time7 = stats.time_by_day(conn, active.id, days=7)
    done7 = stats.tasks_done_by_day(conn, active.id, days=7)
    from .. import logs, sessions
    recent_sessions = sessions.list(conn, project_id=active.id, week=True, limit=20)
    recent_logs = logs.list(conn, project_id=active.id, limit=20)
    data = {
        "minutes_par_jour": time7,
        "taches_faites_par_jour": done7,
        "sessions": [{"date": s.started_at[:10], "min": s.duration_min, "note": s.note} for s in recent_sessions],
        "devlog": [e.content[:120] for e in recent_logs],
    }
    messages = [
        {"role": "system", "content": "Résume la semaine de ce développeur solo en français: accompli, blocages, 2-3 suggestions concrètes."},
        {"role": "user", "content": "Données semaine:\n" + json.dumps(data, ensure_ascii=False)},
    ]
    summary = provider.chat(messages)
    return {"summary": summary, "provider": provider.name, "cloud": ai_status(cfg)["cloud"]}


def plan(conn: sqlite3.Connection, title: str, apply: bool = False, home: Path | None = None) -> dict:
    """Split a big task into subtasks. apply=False -> proposal only (NO write).
    apply=True -> creates the subtasks (explicit human consent: CLI --apply)."""
    cfg, provider = _cfg_and_provider(home)
    _flag(cfg, "plan")
    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": "Contexte:\n" + ctx_mod.build_context(conn) + f"\n\nTâche à découper, titre: {title}"},
    ]
    raw = provider.chat(messages, temperature=0.3)
    proposal = _extract_json(raw).get("subtasks", [])
    result = {"title": title, "proposal": proposal, "applied": False, "created_task_ids": [],
              "provider": provider.name, "cloud": ai_status(cfg)["cloud"]}
    if apply and proposal:
        active = projects.get_active(conn)
        if active is None:
            raise AIDisabledError("aucun projet actif pour --apply")
        for sub in proposal:
            t = tsk.add(conn, active.id, sub.get("title", "sous-tâche"),
                        priority="medium", estimate_min=sub.get("estimate_min"),
                        tags=sub.get("tags") or [])
            result["created_task_ids"].append(t.id)
        result["applied"] = True
    return result


def feature_verdict(conn: sqlite3.Connection, ref, home: Path | None = None) -> dict:
    """Cross tasks + commits + devlog to emit a verdict PROPOSAL for a feature.
    NEVER marks done: calls features.propose() at most."""
    cfg, provider = _cfg_and_provider(home)
    _flag(cfg, "feature_verdict")
    active = projects.get_active(conn)
    if active is None:
        raise AIDisabledError("aucun projet actif")
    f = features.get(conn, ref, active.id)
    if f is None:
        raise AIDisabledError(f"feature {ref!r} introuvable")
    sig = features.check_completion(conn, f.id)
    repo = gitwatch.get_link(conn, active.id)
    linked = []
    if repo:
        linked = [c.message for c in gitwatch.commits(conn, repo_path=repo)
                  if c.parsed_feature and c.parsed_feature.lower() == f.name.lower()][:10]
    mentions = [e.content[:100] for e in ctx_logs_search(conn, f.name, active.id)]
    payload = {
        "feature": f.name,
        "tasks_total": sig["total"], "tasks_done": sig["done"], "all_tasks_done": sig["all_done"],
        "linked_commits": linked, "devlog_mentions": mentions,
    }
    messages = [
        {"role": "system", "content": VERDICT_SYSTEM},
        {"role": "user", "content": "Données feature:\n" + json.dumps(payload, ensure_ascii=False)},
    ]
    raw = provider.chat(messages, temperature=0.2)
    verdict = _extract_json(raw)
    proposed = False
    if verdict.get("seems_complete") and f.status not in ("done", "rejected"):
        f = features.propose(conn, f.id, source="ai",
                             confidence=verdict.get("confidence"),
                             reasoning=verdict.get("reasoning"))
        proposed = True
    return {"verdict": verdict, "feature": f.to_dict(), "proposed": proposed,
            "provider": provider.name, "cloud": ai_status(cfg)["cloud"]}


def ctx_logs_search(conn, text: str, project_id: int):
    from .. import logs
    return logs.search(conn, text, project_id=project_id, limit=5)
