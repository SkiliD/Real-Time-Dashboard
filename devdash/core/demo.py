"""Demo data seeding (step k): `devdash demo seed`.

Fills a fresh install with plausible sample data (2 projects, tasks, a feature,
sessions and devlog entries spread over the last week) so the CLI and GUI can
be explored without a real project. Demo rows are regular rows: wipe the data
folder (~/.devdash) to start clean.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import events, features, logs, projects, tasks


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _days_ago(days: float, minutes: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days, minutes=minutes)


def _insert_session(
    conn: sqlite3.Connection,
    project_id: int,
    task_id: int | None,
    days_ago: float,
    minutes: float,
    note: str,
    kind: str = "focus",
) -> int:
    """Insert an already-stopped session with backdated timestamps."""
    start = _days_ago(days_ago, minutes)
    end = start + timedelta(minutes=minutes)
    cur = conn.execute(
        "INSERT INTO sessions(project_id, task_id, kind, started_at, ended_at, "
        "duration_min, planned_min, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, task_id, kind, _iso(start), _iso(end), round(minutes, 2),
         25 if kind == "focus" else 5, note),
    )
    conn.commit()
    sid = cur.lastrowid
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    events.append(conn, "session", sid, "stopped", dict(row))
    return sid


def _backdate_task_done(conn: sqlite3.Connection, task_id: int, days_ago: float) -> None:
    ts = _iso(_days_ago(days_ago))
    conn.execute(
        "UPDATE tasks SET completed_at=?, updated_at=? WHERE id=?", (ts, ts, task_id)
    )
    conn.commit()


def _backdate_log(conn: sqlite3.Connection, entry_id: int, days_ago: float) -> None:
    conn.execute(
        "UPDATE log_entries SET created_at=? WHERE id=?", (_iso(_days_ago(days_ago)), entry_id)
    )
    conn.commit()


def seed(conn: sqlite3.Connection) -> dict:
    """Create the demo dataset. Returns counts for display."""
    # --- projects (multi-engine, one active) ---
    p1 = projects.create(
        conn, "Aether Vale",
        description="Metroidvania 2D sous Unity. Boucle dash/attaque/esquive.",
        engine="unity", color="#7c5cff", status="development",
    )
    p2 = projects.create(
        conn, "Portfolio Web",
        description="Site perso en HTML/CSS/JS vanilla.",
        engine="web", color="#38bdf8", status="idea",
    )
    projects.set_active(conn, p1.id)

    # --- feature + tasks ---
    f = features.create(conn, p1.id, "dash", "Dash du joueur avec i-frames et cooldown")
    t_dash = tasks.add(conn, p1.id, "Implémenter le dash (vitesse + durée)",
                       priority="critical", estimate_min=90, tags=["gameplay", "combat"],
                       feature_id=f.id, status="done")
    t_iframes = tasks.add(conn, p1.id, "Ajouter les i-frames pendant le dash",
                          priority="high", estimate_min=30, tags=["gameplay"],
                          feature_id=f.id, status="done")
    t_cd = tasks.add(conn, p1.id, "Cooldown visuel du dash (UI)",
                     priority="medium", estimate_min=45, tags=["gameplay", "ui"],
                     feature_id=f.id)
    t_anim = tasks.add(conn, p1.id, "Animation de dash + particules",
                       priority="medium", estimate_min=60, tags=["anim", "vfx"])
    t_audio = tasks.add(conn, p1.id, "SFX de dash",
                        priority="low", estimate_min=15, tags=["audio"])
    t_menu = tasks.add(conn, p1.id, "Menu pause fonctionnel",
                       priority="high", estimate_min=120, tags=["ui", "menu"])
    t_save = tasks.add(conn, p1.id, "Système de sauvegarde (JSON)",
                       priority="high", estimate_min=180, tags=["system"])
    t_web = tasks.add(conn, p2.id, "Page d'accueil du portfolio",
                      priority="medium", estimate_min=90, tags=["ui", "web"])
    features.attach(conn, f.id, t_dash.id, project_id=p1.id)
    features.attach(conn, f.id, t_iframes.id, project_id=p1.id)
    _backdate_task_done(conn, t_dash.id, 5)
    _backdate_task_done(conn, t_iframes.id, 2)
    tasks.start(conn, t_cd.id)

    # --- sessions spread over the last week ---
    _insert_session(conn, p1.id, t_dash.id, 5, 85, "Dash de base OK, à peaufiner sur les pentes")
    _insert_session(conn, p1.id, t_dash.id, 4, 60, "Réglage de la distance de dash")
    _insert_session(conn, p1.id, t_iframes.id, 2, 40, "i-frames actifs, test boss en cours")
    _insert_session(conn, p1.id, t_menu.id, 1, 50, "Pause OK, il manque le retour au menu principal")
    _insert_session(conn, p1.id, None, 0, 25, "Revue du backlog et priorisation")
    _insert_session(conn, p2.id, t_web.id, 3, 30, "Maquette de la page d'accueil", kind="custom")

    # --- devlog entries ---
    e1 = logs.add(conn, "Le dash doit réserver 0.15s d'input buffer, sinon il se sent 'mou'.",
                  project_id=p1.id, task_id=t_dash.id, tags=["decision"])
    e2 = logs.add(conn, "Bloqué: le cooldown du dash ne se reset pas après une mort. À investiguer.",
                  project_id=p1.id, task_id=t_cd.id, tags=["blocker"])
    e3 = logs.add(conn, "Idée: un dash aérien uniquement si on a déjà dash au sol (chaînage).",
                  project_id=p1.id, tags=["idea"])
    e4 = logs.add(conn, "Le projet avance bien, penser à une build jouable d'ici 2 semaines.",
                  project_id=p1.id, tags=["milestone"])
    logs.add(conn, "Première mise en page du portfolio.", project_id=p2.id, task_id=t_web.id)
    _backdate_log(conn, e1.id, 5)
    _backdate_log(conn, e2.id, 1)
    _backdate_log(conn, e3.id, 3)
    _backdate_log(conn, e4.id, 0)

    return {
        "projects": 2,
        "features": 1,
        "tasks": 8,
        "sessions": 6,
        "log_entries": 5,
        "active_project": p1.slug,
    }
