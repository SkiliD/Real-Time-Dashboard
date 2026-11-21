"""Compact project context builder for the AI (privacy/token hygiene).

This is the ONLY function that prepares what is sent to the model: a compact
summary of the active project — never a raw database dump.
"""

from __future__ import annotations

import sqlite3

from .. import features, gitwatch, logs, projects, sessions, stats
from .. import tasks as tsk

DEFAULT_MAX_CHARS = 6000


def build_context(conn: sqlite3.Connection, project_id: int | None = None,
                  max_chars: int = DEFAULT_MAX_CHARS) -> str:
    active = projects.get(conn, project_id) if project_id else projects.get_active(conn)
    if active is None:
        return "Aucun projet actif."
    lines: list[str] = []
    prog = stats.project_progress(conn, active.id)
    lines.append(
        f"Projet actif: {active.name} (moteur/tech: {active.engine}, statut: {active.status}), "
        f"progression estimée {prog['pct_weighted']}% ({prog['done']}/{prog['total']} tâches faites)"
    )
    open_tasks = tsk.list(conn, project_id=active.id, status=["todo", "in_progress"],
                          include_cancelled=False)
    if open_tasks:
        lines.append("Tâches ouvertes: " + "; ".join(
            f"#{t.id} {t.title} [{t.priority}/{t.status}]" for t in open_tasks[:12]
        ))
    else:
        lines.append("Tâches ouvertes: aucune")
    s = sessions.current(conn)
    if s is not None and s.project_id == active.id:
        extra = f", tâche #{s.task_id}" if s.task_id else ""
        lines.append(f"Session en cours: #{s.id} ({s.kind}), {sessions.elapsed_min(s):.0f} min écoulées{extra}")
    lines.append(
        f"Temps aujourd'hui: {stats.today_minutes(conn, active.id):.0f} min · "
        f"streak: {stats.streak_days(conn, active.id)} jour(s) · "
        f"tâches terminées sur 7 jours: {sum(d['count'] for d in stats.tasks_done_by_day(conn, active.id, 7))}"
    )
    recent_logs = logs.list(conn, project_id=active.id, limit=5)
    if recent_logs:
        lines.append("Devlog récent: " + " | ".join(e.content[:80] for e in recent_logs))
    feats_open = features.list(conn, project_id=active.id, status="open")
    feats_proposed = features.list(conn, project_id=active.id, status="proposed")
    if feats_open or feats_proposed:
        lines.append("Features: " + "; ".join(
            f"{f['name']} ({f['status']}, {f['progress']['done']}/{f['progress']['total']} tâches)"
            for f in feats_open + feats_proposed
        ))
    repo = gitwatch.get_link(conn, active.id)
    if repo:
        st = gitwatch.status(conn, active.id)
        last = st.get("last_commit") or {}
        if last:
            lines.append(f"Git: branche {st.get('branch')}, dernier commit {last.get('short')} « {str(last.get('message'))[:60]} »")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…[contexte tronqué]"
    return text
