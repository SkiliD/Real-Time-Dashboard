# REST endpoints consumed by app.js

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from devdash.core import config, db
from devdash.core.ai import assistant
from devdash.core import events as ev
from devdash.core import features as feats
from devdash.core import gitwatch
from devdash.core import logs
from devdash.core import projects as proj
from devdash.core import sessions as sess
from devdash.core import stats
from devdash.core import tasks as tsk


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _folder_picker_command(title: str, platform: str | None = None) -> list[str] | None:
    """OS-native folder-picker command (stdlib subprocess only — the HTTP server
    thread must not touch tkinter). Prints the chosen path on stdout, nothing
    when cancelled. `platform` is injectable for tests."""
    plat = platform or sys.platform
    if plat == "win32":
        # -Sta: FolderBrowserDialog needs an STA thread; title via env var to
        # avoid any quoting issue in the PowerShell snippet
        return ["powershell", "-NoProfile", "-Sta", "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$d.Description = $env:DEVDASH_PICK_TITLE; "
                "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $d.SelectedPath }"]
    if plat == "darwin":
        return ["osascript", "-e",
                "on run argv\n"
                'tell application "System Events" to activate\n'
                "set p to choose folder with prompt (item 1 of argv)\n"
                "POSIX path of p\n"
                "end run",
                "--", title]
    if shutil.which("zenity"):
        return ["zenity", "--file-selection", "--directory", f"--title={title}"]
    return None


def _general_block(cfg, home) -> dict:
    """The non-AI part of the settings payload (shared by get and save)."""
    from devdash.core import i18n
    return {
        "lang": i18n.get_lang(home),
        "langs": list(i18n.SUPPORTED_LANGS),
        "pomodoro_focus_min": cfg.pomodoro_focus_min,
        "pomodoro_break_min": cfg.pomodoro_break_min,
        "git_watch_interval_sec": cfg.git_watch_interval_sec,
        "gui_port": cfg.gui_port,
        "theme": cfg.theme,
        "commit_patterns": list(cfg.commit_patterns),
    }


class Api:
    def __init__(self, home: Path | None = None):
        self.home = home  # None -> resolved from DEVDASH_HOME at call time
        self._db_ready = False
        self._db_lock = threading.Lock()

    @contextmanager
    def _conn(self):
        home = self.home or config.devdash_home()
        conn = db.connect(config.db_path(home))
        try:
            # init_db runs the full schema script + a write commit: do it ONCE per
            # process, not on every request (the event poll hits this 1.5s apart)
            if not self._db_ready:
                with self._db_lock:
                    if not self._db_ready:
                        db.init_db(conn)
                        ev.prune(conn)  # bound the append-only event bus once per process
                        self._db_ready = True
            yield conn
        finally:
            conn.close()  # always closed, even if init_db raised

    # --- input helpers: bad client input is a 400, never a 500 -------------

    # list endpoints never return more than this many rows (SQLite LIMIT -1
    # would mean "unlimited" — a client must not be able to ask for that)
    MAX_LIST_LIMIT = 1000

    @staticmethod
    def _int(value, what: str, default=None, hi: int | None = None):
        if value is None or value == "":
            return default
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ApiError(f"invalid {what}: {value!r} (expected an integer)")
        if hi is not None:
            n = max(1, min(hi, n))
        return n

    @staticmethod
    def _str_list(body: dict, key: str) -> list[str] | None:
        """Fetch an optional list[str] field; wrong type is a clean 400."""
        if key not in body or body[key] is None:
            return None
        v = body[key]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ApiError(f"invalid {key}: expected a list of strings")
        return v

    @staticmethod
    def _str(body: dict, key: str, default: str = "", required: bool = False, max_len: int = 100_000) -> str:
        """Fetch a string field; wrong type is a clean 400, never a 500."""
        if key not in body or body[key] is None:
            if required:
                raise ApiError(f"{key} is required")
            return default
        v = body[key]
        if not isinstance(v, str):
            raise ApiError(f"invalid {key}: expected a string, got {type(v).__name__}")
        if len(v) > max_len:
            raise ApiError(f"{key} is too long (max {max_len} chars)")
        return v

    @staticmethod
    def _tags(value) -> list[str]:
        """Validate a tags field: must be a list of strings (a bare string is NOT
        split into characters — that was a bug)."""
        if value is None:
            return []
        if isinstance(value, str) or not isinstance(value, list) \
                or not all(isinstance(t, str) for t in value):
            raise ApiError("tags must be a list of strings")
        return value

    def _list_pid(self, c, query: dict) -> int | None:
        """Scope for list endpoints: explicit project=... wins; 'all'/'*' means
        every project; otherwise the ACTIVE project (the GUI header shows the
        active project, so lists must match it — not silently mix all projects)."""
        ref = query.get("project")
        if ref in (None, ""):
            active = proj.get_active(c)
            return active.id if active else None
        if ref in ("all", "*"):
            return None
        return self._resolve_project(c, ref).id

    # --- projects ---------------------------------------------------------

    def projects(self) -> dict:
        with self._conn() as c:
            active = proj.get_active(c)
            return {
                "active": active.slug if active else None,
                "projects": [p.to_dict() for p in proj.list(c, include_archived=True)],
            }

    def projects_create(self, body: dict) -> dict:
        name = self._str(body, "name", required=True).strip()
        if not name:
            raise ApiError("name is required")
        try:
            with self._conn() as c:
                p = proj.create(
                    c, name,
                    description=self._str(body, "description"),
                    engine=self._str(body, "engine", "other") or "other",
                    color=self._str(body, "color", "#8a8f98") or "#8a8f98",
                    status=self._str(body, "status", "idea") or "idea",
                    languages=self._str_list(body, "languages"),
                )
                if body.get("set_active"):
                    proj.set_active(c, p.id)
                return p.to_dict()
        except (KeyError, ValueError) as e:
            raise ApiError(str(e))

    def projects_set_active(self, body: dict) -> dict:
        ref = body.get("ref")
        if ref is None:
            raise ApiError("missing 'ref'")
        with self._conn() as c:
            try:
                p = proj.set_active(c, int(ref) if str(ref).isdigit() else str(ref))
            except KeyError:
                raise ApiError(f"no project matching {ref!r}", status=404)
            return {"active": p.to_dict()}

    def projects_update(self, body: dict) -> dict:
        ref = body.get("ref")
        with self._conn() as c:
            try:
                p = proj.update(
                    c, int(ref) if str(ref).isdigit() else str(ref),
                    name=body.get("name"), description=body.get("description"),
                    engine=body.get("engine"), color=body.get("color"),
                    status=body.get("status"),
                    archived=body.get("archived"),
                    languages=self._str_list(body, "languages"),
                )
            except KeyError:
                raise ApiError(f"no project matching {ref!r}", status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return p.to_dict()

    def detect_path(self, query: dict) -> dict:
        """Probe an existing folder: engine markers, suggested name, git root.
        Read-only filesystem probing; bounded input."""
        from devdash.core import detect
        raw = query.get("path") or ""
        if not isinstance(raw, str):
            raise ApiError("invalid path: expected a string")
        if len(raw) > 4096:
            raise ApiError("path too long")
        return detect.detect_project(raw)

    def projects_connect(self, body: dict) -> dict:
        """Connect an EXISTING project folder: create the DevDash project with
        auto-detected engine/name, and auto-link the enclosing git repo
        (read-only) unless no_git is set."""
        from devdash.core import config as _cfg, detect, gitwatch
        path = self._str(body, "path", required=True).strip()
        if not path:
            raise ApiError("path is required")
        info = detect.detect_project(path)
        if not info["is_dir"]:
            raise ApiError(f"not a directory: {path}")
        with self._conn() as c:
            engine = (self._str(body, "engine") or info["engine"] or "other").lower()
            try:
                p = proj.create(
                    c, (self._str(body, "name").strip() or info["name"] or info["path"]),
                    description=self._str(body, "description"),
                    engine=engine,
                    color=self._str(body, "color", "#8a8f98") or "#8a8f98",
                    status=self._str(body, "status", "idea") or "idea",
                    languages=self._str_list(body, "languages"),
                )
            except ValueError as e:
                raise ApiError(str(e))
            if body.get("set_active"):
                proj.set_active(c, p.id)
            git_root = None
            if not body.get("no_git") and info["git_root"]:
                try:
                    git_root = gitwatch.link(c, p.id, info["git_root"])
                    gitwatch.sync_repo(c, git_root, _cfg.load_config(self.home).commit_patterns)
                except gitwatch.GitError as e:
                    git_root = None  # link failed: report it, don't fail the connect
                    info["git_error"] = str(e)
            return {"project": p.to_dict(), "detected": info, "git_root": git_root}

    def projects_delete(self, body: dict) -> dict:
        """Hard delete (explicit user action; the GUI confirms first)."""
        ref = body.get("ref")
        if ref is None:
            raise ApiError("missing 'ref'")
        with self._conn() as c:
            try:
                p = proj.delete(c, int(ref) if str(ref).isdigit() else str(ref))
            except KeyError:
                raise ApiError(f"no project matching {ref!r}", status=404)
            active = proj.get_active(c)
            return {"deleted": p.slug, "active": active.slug if active else None}

    # --- tasks ------------------------------------------------------------

    def _resolve_project(self, c, ref):
        if ref:
            p = proj.get(c, int(ref) if str(ref).isdigit() else str(ref))
            if p is None:
                raise ApiError(f"no project matching {ref!r}", status=404)
            return p
        p = proj.get_active(c)
        if p is None:
            raise ApiError("no active project (create one or pass project=...)", status=400)
        return p

    def tags(self, query: dict) -> dict:
        """All distinct task tags (unfiltered) — feeds the GUI tag filter select,
        which must list every tag even while a tag filter is active."""
        with self._conn() as c:
            pid = self._list_pid(c, query)
            return {"tags": tsk.list_tags(c, project_id=pid)}

    def tasks(self, query: dict) -> dict:
        with self._conn() as c:
            pid = self._list_pid(c, query)  # defaults to the ACTIVE project
            raw_status = query.get("status")
            statuses = [s for s in raw_status.split(",") if s] if raw_status else None
            result = tsk.list(
                c, project_id=pid, status=statuses,
                priority=query.get("priority") or None,
                tag=query.get("tag") or None,
                feature_id=self._int(query.get("feature"), "feature"),
                include_cancelled=query.get("all") in ("1", "true", "yes"),
                q=query.get("q") or None,
            )
            return {"count": len(result), "tasks": [t.to_dict() for t in result]}

    def tasks_update(self, body: dict) -> dict:
        """Edit a task's fields. Only keys present in the body are changed;
        explicit null clears parent_id / feature_id (set to NULL)."""
        tid = self._int(body.get("id"), "id")
        if tid is None:
            raise ApiError("id is required")
        kwargs = {}
        for key in ("title", "description", "priority"):  # status changes go through /api/tasks/status
            if key in body:
                kwargs[key] = self._str(body, key)  # wrong type -> clean 400, not a 500
        if "estimate_min" in body:
            kwargs["estimate_min"] = self._int(body["estimate_min"], "estimate_min")
        if "tags" in body:
            kwargs["tags"] = self._tags(body["tags"])
        if "parent_id" in body:
            kwargs["parent_id"] = self._int(body["parent_id"], "parent_id")  # None clears
        if "feature_id" in body:
            kwargs["feature_id"] = self._int(body["feature_id"], "feature_id")
        with self._conn() as c:
            try:
                t = tsk.update(c, tid, **kwargs)
            except KeyError as e:
                # relay the core message verbatim ("no task #N" vs "no parent task #M")
                raise ApiError(str(e).strip("'\"") or f"no task #{tid}", status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return t.to_dict()

    def tasks_delete(self, body: dict) -> dict:
        """Hard-delete a task (subtasks cascade). Explicit user action only."""
        tid = self._int(body.get("id"), "id")
        if tid is None:
            raise ApiError("id is required")
        with self._conn() as c:
            try:
                t = tsk.delete(c, tid)
            except KeyError:
                raise ApiError(f"no task #{tid}", status=404)
            return {"deleted": t.id, "title": t.title}

    def tasks_add(self, body: dict) -> dict:
        title = self._str(body, "title", required=True).strip()
        if not title:
            raise ApiError("title is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                t = tsk.add(
                    c, p.id, title,
                    description=self._str(body, "description"),
                    priority=self._str(body, "priority", "medium") or "medium",
                    estimate_min=self._int(body.get("estimate_min"), "estimate_min"),
                    tags=self._tags(body.get("tags")),
                    parent_id=self._int(body.get("parent_id"), "parent_id"),
                )
            except (KeyError, ValueError) as e:
                raise ApiError(str(e).strip("'\""))
            return {**t.to_dict(), "project": p.slug}

    def tasks_set_status(self, body: dict) -> dict:
        try:
            tid = int(body["id"])
            status = body["status"]
        except (KeyError, ValueError):
            raise ApiError("body must contain {id, status}")
        with self._conn() as c:
            try:
                t = tsk.start(c, tid) if status == "in_progress" else tsk.set_status(c, tid, status)
            except KeyError as e:
                raise ApiError(str(e).strip("'\"") or f"no task #{tid}", status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return t.to_dict()

    # --- stats --------------------------------------------------------

    def stats_overview(self, query: dict) -> dict:
        with self._conn() as c:
            project = query.get("project")
            explicit_all = project in ("all", "*")
            if explicit_all:
                pid = None  # aggregate across ALL projects
            elif project:
                pid = self._resolve_project(c, project).id
            else:
                active = proj.get_active(c)
                pid = active.id if active else None
            days = self._int(query.get("days"), "days", 14)
            if days is None or not (1 <= days <= 365):
                raise ApiError("days must be between 1 and 365")
            group = (query.get("group") or "day").lower()
            if group not in ("day", "week", "month"):
                raise ApiError("group must be day, week or month")
            if pid is None and not explicit_all:
                return {"project": None, "group": group, "days": days, "time_by_day": [],
                        "tasks_done_by_day": [], "by_project": stats.minutes_by_project(c, days),
                        "minutes": {"today": 0, "week": 0, "month": 0, "all": 0},
                        "streak_days": 0, "progress": None, "by_tag": [], "tasks": {"open": 0, "done": 0}}
            all_tasks = tsk.list(c, project_id=pid, include_cancelled=False)
            time_rows = stats.time_by_day(c, pid, days)
            done_rows = stats.tasks_done_by_day(c, pid, days)
            return {
                "project": pid,
                "group": group,
                "days": days,
                # regrouped in place when group != day: same keys, label in "date"
                "time_by_day": stats.regroup_rows(time_rows, group, "minutes"),
                "tasks_done_by_day": stats.regroup_rows(done_rows, group, "count"),
                "by_project": stats.minutes_by_project(c, days),
                "minutes": {
                    "today": stats.today_minutes(c, pid),
                    "week": stats.period_minutes(c, pid, 7),
                    "month": stats.period_minutes(c, pid, 30),
                    "all": stats.period_minutes(c, pid, 3650),
                },
                "streak_days": stats.streak_days(c, pid),
                "progress": stats.project_progress(c, pid) if pid is not None else None,
                "by_tag": stats.by_tag(c, pid),
                "tasks": {
                    "open": sum(1 for t in all_tasks if t.status in ("todo", "in_progress")),
                    "done": sum(1 for t in all_tasks if t.status == "done"),
                },
            }

    # --- AI assistant --------------------------------------

    def ai_status(self) -> dict:
        from devdash.core.ai import ai_status as _ai_status
        return _ai_status(config.load_config(self.home))

    def ai_context(self) -> dict:
        """Preview of the compact context that would be sent to the model.
        Purely local read — works with the AI disabled."""
        with self._conn() as c:
            return assistant.context_preview(c, home=self.home)

    # --- AI configuration from the GUI (settings tab) ----------------------

    def ai_providers(self) -> dict:
        from devdash.core.ai import provider_catalog
        return {"providers": provider_catalog()}

    def ai_config_get(self) -> dict:
        """Current AI settings. The API key is write-only: the response says
        whether one is set, never its value."""
        from devdash.core.ai import public_ai_config
        return public_ai_config(config.load_config(self.home), home=self.home)

    def ai_config_save(self, body: dict) -> dict:
        from devdash.core.ai import apply_ai_settings, public_ai_config
        cfg = config.load_config(self.home)
        try:
            apply_ai_settings(cfg, body)
        except ValueError as e:
            raise ApiError(str(e))  # validation messages never contain the key
        config.save_config(cfg, self.home)  # chmod 600: may hold an API key
        return {"saved": True, "config": public_ai_config(cfg, home=self.home),
                "status": self.ai_status()}

    # --- general settings (GUI settings tab, beyond AI) --------------------

    def settings_get(self) -> dict:
        """Full public settings: AI block (key write-only) + general block."""
        from devdash.core.ai import public_ai_config
        cfg = config.load_config(self.home)
        home = self.home or config.devdash_home()
        return {
            "home": str(home),
            "ai": public_ai_config(cfg, home=home),
            "general": _general_block(cfg, home),
        }

    def settings_save(self, body: dict) -> dict:
        """Save general + AI settings in one shot (the settings tab form)."""
        from devdash.core import i18n
        from devdash.core.ai import apply_ai_settings, public_ai_config
        cfg = config.load_config(self.home)
        try:
            config.apply_general_settings(cfg, body or {}, allowed_langs=i18n.SUPPORTED_LANGS)
            apply_ai_settings(cfg, body or {})
        except ValueError as e:
            raise ApiError(str(e))  # validation messages never contain the key
        config.save_config(cfg, self.home)
        home = self.home or config.devdash_home()
        return {"saved": True, "settings": {
            "home": str(home),
            "ai": public_ai_config(cfg, home=home),
            "general": _general_block(cfg, home),
        }, "status": self.ai_status()}

    def _confined_out(self, body: dict | None, default_subdir: str):
        """Resolve the optional `out` path from a POST body. The file may only
        be written INSIDE DEVDASH_HOME — this endpoint is a local write
        primitive and must not become an arbitrary-path write."""
        home = Path(self.home or config.devdash_home()).resolve()
        raw = (body or {}).get("out")
        if not raw:
            return home / default_subdir
        out = Path(raw).expanduser().resolve()
        if not out.is_relative_to(home):
            raise ApiError(f"out must be inside DEVDASH_HOME ({home})", status=400)
        return out

    def backup_now(self, body: dict) -> dict:
        """One-click backup from the GUI: timestamped zip of the whole home.
        Default destination is <DEVDASH_HOME>/backups (never the cwd)."""
        from devdash.core.backup import make_backup
        home = self.home or config.devdash_home()
        try:
            return make_backup(home=home, out_dir=self._confined_out(body, "backups"))
        except OSError as e:
            raise ApiError(f"backup failed: {e}")

    def export_now(self, body: dict) -> dict:
        """One-click JSON export from the GUI (read-only)."""
        from devdash.core.backup import export_json
        home = self.home or config.devdash_home()
        try:
            return export_json(home=home, out_dir=self._confined_out(body, "exports"))
        except OSError as e:
            raise ApiError(f"export failed: {e}")

    # --- self-update ---------------------------------------------------------

    def update_status(self) -> dict:
        """Version + install mode + local git info. Read-only, no network."""
        from devdash import __version__
        from devdash.core import updater
        return {"version": __version__, **updater.git_status()}

    def update_check(self, body: dict | None = None) -> dict:
        """git fetch + compare with upstream (network, read-only on the tree)."""
        from devdash.core import updater
        return updater.check_for_updates()

    def update_now(self, body: dict) -> dict:
        """Apply the update: git pull --ff-only + pip install -e . (git mode).
        Aborts on a dirty tree. Mutating route: Host/Origin guards apply, AND
        it requires an explicit confirm=true (the GUI arms the button first,
        same two-step pattern as delete/demo)."""
        from devdash.core import updater
        if not (body or {}).get("confirm"):
            raise ApiError("update requires confirm=true (two-step in the GUI)", status=400)
        return updater.update(reinstall=bool((body or {}).get("reinstall", True)))

    def open_home(self, body: dict | None = None) -> dict:
        """Reveal DEVDASH_HOME in the OS file explorer. The folder is only opened
        when the client explicitly sends open=true (GUI button); otherwise the
        endpoint just returns the path."""
        import os
        import subprocess
        import sys

        home = Path(self.home or config.devdash_home()).resolve()
        if (body or {}).get("open"):
            try:
                if sys.platform == "win32" and hasattr(os, "startfile"):
                    os.startfile(home)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(home)])
                else:
                    subprocess.Popen(["xdg-open", str(home)])
            except OSError as e:
                raise ApiError(f"cannot open folder: {e}")
        return {"path": str(home)}

    def demo_seed(self, body: dict) -> dict:
        """Seed demo data from the GUI. Refuses when projects already exist
        unless force=true (same rule as `devdash demo seed --force`)."""
        from devdash.core import demo as demo_core
        with self._conn() as c:
            if proj.list(c, include_archived=True) and not body.get("force"):
                raise ApiError("projects already exist — pass force=true to add demo data anyway")
            return demo_core.seed(c)

    def ai_config_test(self, body: dict) -> dict:
        """Dry-run a connection with the given (not yet saved) settings.
        Sends only a tiny fixed prompt — never project context — so testing a
        cloud provider leaks nothing. The API key is scrubbed from errors."""
        from devdash.core.ai import apply_ai_settings, get_provider, is_cloud
        cfg = config.load_config(self.home)
        try:
            apply_ai_settings(cfg, body or {})
        except ValueError as e:
            raise ApiError(str(e))
        if not cfg.ai_enabled:
            raise ApiError("enable the AI first (ai_enabled) to test the connection")
        provider = get_provider(cfg)
        if provider is None:
            raise ApiError("AI is disabled")
        try:
            reply = provider.chat([{"role": "user", "content": "Reply with exactly: OK"}],
                                  temperature=0.0)
        except Exception as e:  # AIError or network error: scrub the key, then 400
            msg = str(e)
            if cfg.ai_api_key:
                msg = msg.replace(cfg.ai_api_key, "***")
            raise ApiError(f"connection failed: {msg[:300]}")
        return {"ok": True, "provider": cfg.ai_provider, "model": cfg.ai_model,
                "cloud": is_cloud(cfg), "reply": (reply or "")[:200]}

    def _ai_call(self, fn, *args, **kwargs):
        from devdash.core.ai.provider import AIDisabledError, AIError
        with self._conn() as c:
            try:
                return fn(c, *args, home=self.home, **kwargs)
            except (AIDisabledError, AIError) as e:
                raise ApiError(str(e), status=400)

    def ai_ask(self, body: dict) -> dict:
        q = self._str(body, "question", required=True).strip()
        if not q:
            raise ApiError("question is required")
        return self._ai_call(assistant.ask, q)

    def ai_summarize(self, body: dict) -> dict:
        return self._ai_call(assistant.summarize_week)

    def ai_plan(self, body: dict) -> dict:
        title = self._str(body, "title", required=True).strip()
        if not title:
            raise ApiError("title is required")
        # apply=True is the explicit human consent (GUI confirm button)
        return self._ai_call(assistant.plan, title, apply=bool(body.get("apply")))

    def ai_suggest(self, body: dict) -> dict:
        """Analyze the project and propose tickets. READ-ONLY: never creates anything."""
        return self._ai_call(assistant.suggest)

    def ai_suggest_apply(self, body: dict) -> dict:
        """Create tickets from AI proposals — EXPLICIT human consent only: the GUI
        calls this solely from the "create checked tickets" button. Every item is
        re-validated server-side before touching the DB (whitelisted fields)."""
        items = body.get("tasks")
        if not isinstance(items, list) or not items:
            raise ApiError("tasks is required (non-empty list)")
        if len(items) > assistant.MAX_SUGGEST_APPLY:
            raise ApiError(f"too many tasks at once (max {assistant.MAX_SUGGEST_APPLY})")
        clean = []
        for it in items:
            if not isinstance(it, dict):
                raise ApiError("each task must be an object")
            clean.append({k: it.get(k) for k in ("title", "description", "priority", "estimate_min", "tags")})
        return self._ai_call(assistant.apply_suggestions, clean)

    def ai_verdict(self, body: dict) -> dict:
        ref = body.get("ref")
        if not ref:
            raise ApiError("ref is required")
        return self._ai_call(assistant.feature_verdict, ref)

    def ai_task_verdict(self, body: dict) -> dict:
        """AI verdict on a task. The GUI NEVER applies it: apply is refused here
        on purpose — the approve button in the modal performs the status change
        itself, so a human always makes the final call."""
        from devdash.core.ai.provider import AIDisabledError, AIError
        tid = self._int(body.get("id"), "id")
        if tid is None:
            raise ApiError("id is required")
        with self._conn() as c:
            try:
                return assistant.task_verdict(c, tid, apply=False, home=self.home)
            except (AIDisabledError, AIError) as e:
                raise ApiError(str(e), status=400)
            except KeyError:
                raise ApiError(f"no task #{tid}", status=404)

    # --- features ---------------------------------------------------

    def features_list(self, query: dict) -> dict:
        with self._conn() as c:
            pid = self._list_pid(c, query)  # defaults to the ACTIVE project
            rows = feats.list(c, project_id=pid, status=query.get("status") or None)
            return {"count": len(rows), "features": rows}

    def features_add(self, body: dict) -> dict:
        name = self._str(body, "name", required=True).strip()
        if not name:
            raise ApiError("name is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                f = feats.create(c, p.id, name, self._str(body, "description"))
            except (KeyError, ValueError) as e:
                raise ApiError(str(e).strip("'\""))
            return {**f.to_dict(), "project": p.slug, "progress": feats.progress(c, f.id)}

    def _feature_ref(self, c, body):
        p = self._resolve_project(c, body.get("project"))
        f = feats.get(c, body.get("ref"), p.id)
        if f is None:
            raise ApiError(f"no feature matching {body.get('ref')!r}", status=404)
        return p, f

    def features_attach(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            tid = self._int(body.get("task_id"), "task_id")
            if tid is None:
                raise ApiError("task_id is required")
            try:
                f = feats.attach(c, f.id, tid)
            except KeyError as e:
                raise ApiError(str(e).strip("'\""), status=404)
            except ValueError as e:  # e.g. task belongs to another project
                raise ApiError(str(e))
            return {**f.to_dict(), "progress": feats.progress(c, f.id)}

    def features_done(self, body: dict) -> dict:
        """MANUAL validation (human decision) — the only path to done."""
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.approve(c, f.id, note=body.get("note", ""))
            return f.to_dict()

    def features_propose(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.propose(c, f.id, source=body.get("source", "ai"),
                              confidence=body.get("confidence"), reasoning=body.get("reasoning"))
            return f.to_dict()

    def features_approve(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.approve(c, f.id, note=body.get("note", ""))
            return f.to_dict()

    def features_reject(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.reject(c, f.id, feedback=body.get("feedback", ""))
            return f.to_dict()

    def features_reopen(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            f = feats.reopen(c, f.id)
            return f.to_dict()

    def features_update(self, body: dict) -> dict:
        """Rename / edit a feature (name unique per project)."""
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            kwargs = {}
            if "name" in body:
                kwargs["name"] = self._str(body, "name")
            if "description" in body:
                kwargs["description"] = self._str(body, "description")
            try:
                f = feats.update(c, f.id, p.id, **kwargs)
            except (KeyError, ValueError) as e:
                raise ApiError(str(e).strip("'\""))
            return f.to_dict()

    def features_delete(self, body: dict) -> dict:
        """Hard delete (explicit user action; the GUI confirms first). Tasks keep existing (detached)."""
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            try:
                f = feats.delete(c, f.id, p.id)
            except KeyError as e:
                raise ApiError(str(e).strip("'\""), status=404)
            return {"deleted": f.id, "name": f.name}

    def features_detach(self, body: dict) -> dict:
        with self._conn() as c:
            p, f = self._feature_ref(c, body)
            tid = self._int(body.get("task_id"), "task_id")
            if tid is None:
                raise ApiError("task_id is required")
            try:
                f = feats.detach(c, f.id, tid, project_id=p.id)
            except KeyError as e:
                raise ApiError(str(e).strip("'\""), status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return {**f.to_dict(), "progress": feats.progress(c, f.id)}

    def features_show(self, query: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, query.get("project"))
            f = feats.get(c, query.get("ref"), p.id)
            if f is None:
                raise ApiError(f"no feature matching {query.get('ref')!r}", status=404)
            return {**f.to_dict(), "progress": feats.progress(c, f.id),
                    "completion_signal": feats.check_completion(c, f.id)}

    # --- git -------------------------------------------

    def git_link(self, body: dict) -> dict:
        path = self._str(body, "path", required=True)
        if not path.strip():
            raise ApiError("path is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                repo = gitwatch.link(c, p.id, path)
            except (gitwatch.GitError, KeyError) as e:
                raise ApiError(str(e))
            return {"project": p.slug, "repo_path": repo}

    def git_unlink(self, body: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            gitwatch.unlink(c, p.id)
            return {"project": p.slug, "linked": False}

    def git_sync(self, body: dict) -> dict:
        with self._conn() as c:
            if body.get("project"):
                p = self._resolve_project(c, body["project"])
                repo = gitwatch.get_link(c, p.id)
                if repo is None:
                    raise ApiError(f"project {p.slug} has no linked repo", status=400)
                try:
                    r = gitwatch.sync_repo(c, repo, config.load_config(self.home).commit_patterns)
                except gitwatch.GitError as e:  # same error collection as sync_all
                    return {"repos": [], "errors": [{"project_id": p.id,
                                                      "repo_path": repo, "error": str(e)}]}
                return {"repos": [r], "errors": []}
            return gitwatch.sync_all(c, home=self.home)

    def git_commits(self, query: dict) -> dict:
        with self._conn() as c:
            pid = self._list_pid(c, query)  # defaults to the ACTIVE project
            repo = gitwatch.get_link(c, pid) if pid else None
            rows = gitwatch.commits(
                c, repo_path=repo, project_id=pid,
                task_id=self._int(query.get("task"), "task"),
                unparsed=query.get("unparsed") in ("1", "true", "yes"),
                limit=self._int(query.get("limit"), "limit", 100, hi=Api.MAX_LIST_LIMIT),
            )
            return {"count": len(rows), "commits": [r.to_dict() for r in rows]}

    def git_status(self, query: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, query.get("project"))
            data = gitwatch.status(c, p.id)
            data["project"] = p.slug
            return data

    def git_repos(self) -> dict:
        """All linked repos (project_id + path). Read-only."""
        with self._conn() as c:
            return {"repos": [{"project_id": pid, "repo_path": path}
                              for pid, path in gitwatch.linked_repos(c)]}

    def files_list(self) -> dict:
        """List backup zips and JSON exports inside DEVDASH_HOME (never outside)."""
        home = Path(self.home or config.devdash_home()).resolve()
        out = {"home": str(home), "backups": [], "exports": []}
        for sub, key in (("backups", "backups"), ("exports", "exports")):
            d = home / sub
            if d.is_dir():
                for f in sorted(d.iterdir(), key=lambda p: p.name, reverse=True)[:50]:
                    if f.is_file() and f.suffix in (".zip", ".json"):
                        st = f.stat()
                        out[key].append({"name": f.name, "bytes": st.st_size,
                                         "path": str(f)})
        return out

    # --- filesystem: native folder picker -----------------------------------

    def fs_pick_directory(self, body: dict) -> dict:
        """Open the OS folder picker on the desktop where the server runs.

        Returns {"path": str | null} (null = cancelled). The GUI only pre-fills
        the path input with the result — the user still confirms the link, so
        this endpoint never connects a project or a repo by itself.
        """
        title = self._str(body, "title") or "Select a folder"
        if len(title) > 120:
            raise ApiError("title is too long")
        cmd = _folder_picker_command(title)
        if cmd is None:
            raise ApiError("no folder picker available on this platform")
        env = dict(os.environ, DEVDASH_PICK_TITLE=title)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
        except (OSError, subprocess.TimeoutExpired):
            raise ApiError("folder picker could not be started")
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        return {"path": lines[-1] if lines else None}

    def git_ignore(self, body: dict) -> dict:
        """Add/refresh the marker-delimited DevDash block in the linked repo's
        .gitignore (idempotent). Explicit user action; touches no git history."""
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            linked = gitwatch.get_link(c, p.id)
            repo = self._str(body, "repo") or linked
            # confinement: the .gitignore write may only target the repo LINKED
            # to this project — never an arbitrary path on disk
            if linked and repo != linked:
                raise ApiError("repo must be the repo linked to this project", status=400)
            if not repo:
                raise ApiError(f"project {p.slug} has no linked repo", status=400)
            home = self.home or config.devdash_home()
            try:
                r = gitwatch.ensure_gitignore(repo, home=home)
            except gitwatch.GitError as e:
                raise ApiError(str(e))
            r["project"] = p.slug
            return r

    # --- devlog ---------------------------------------------------

    def logs_list(self, query: dict) -> dict:
        with self._conn() as c:
            pid = self._list_pid(c, query)  # defaults to the ACTIVE project
            rows = logs.list(
                c, project_id=pid,
                task_id=self._int(query.get("task"), "task"),
                query=query.get("q") or None,
                limit=self._int(query.get("limit"), "limit", 200, hi=Api.MAX_LIST_LIMIT),
            )
            return {"count": len(rows), "entries": [r.to_dict() for r in rows]}

    def logs_add(self, body: dict) -> dict:
        content = self._str(body, "content", required=True).strip()
        if not content:
            raise ApiError("content is required")
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                e = logs.add(c, content, project_id=p.id,
                             task_id=self._int(body.get("task_id"), "task_id"),
                             tags=self._tags(body.get("tags")))
            except KeyError as e2:
                raise ApiError(str(e2).strip("'\""))
            except ValueError as e:
                raise ApiError(str(e))
            return {**e.to_dict(), "project": p.slug}

    def logs_update(self, body: dict) -> dict:
        eid = self._int(body.get("id"), "id")
        if eid is None:
            raise ApiError("id is required")
        with self._conn() as c:
            try:
                e = logs.update(c, eid,
                                content=self._str(body, "content") if "content" in body else None,
                                tags=self._tags(body["tags"]) if "tags" in body else None)
            except KeyError:
                raise ApiError(f"no log entry #{eid}", status=404)
            except ValueError as e:
                raise ApiError(str(e))
            return e.to_dict()

    def logs_delete(self, body: dict) -> dict:
        eid = self._int(body.get("id"), "id")
        if eid is None:
            raise ApiError("id is required")
        with self._conn() as c:
            try:
                e = logs.delete(c, eid)
            except KeyError:
                raise ApiError(f"no log entry #{eid}", status=404)
            return {"deleted": e.id}

    # --- sessions -------------------------------------------------

    def session_start(self, body: dict) -> dict:
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            try:
                s = sess.start(
                    c, p.id,
                    task_id=self._int(body.get("task_id"), "task_id"),
                    kind=self._str(body, "kind", "focus") or "focus",
                    minutes=self._int(body.get("minutes"), "minutes"),
                )
            except (KeyError, ValueError) as e:
                raise ApiError(str(e).strip("'\""))
            return {**s.to_dict(), "project": p.slug}

    def session_stop(self, body: dict) -> dict:
        with self._conn() as c:
            try:
                s = sess.stop(c, self._int(body.get("id"), "id"), note=body.get("note", ""))
            except KeyError as e:
                # "no running session" is a client error (400), not a missing row
                msg = str(e).strip("'\"")
                raise ApiError(msg, status=400 if "running" in msg else 404)
            except ValueError as e:
                raise ApiError(str(e))
            return s.to_dict()

    def session_current(self) -> dict:
        with self._conn() as c:
            s = sess.current(c)
            if s is None:
                return {"running": False, "session": None, "elapsed_min": 0}
            return {"running": True, "session": s.to_dict(),
                    "elapsed_min": sess.elapsed_min(s)}

    def sessions_list(self, query: dict) -> dict:
        with self._conn() as c:
            pid = self._list_pid(c, query)  # defaults to the ACTIVE project
            rows = sess.list(
                c, project_id=pid,
                task_id=self._int(query.get("task"), "task"),
                today=query.get("today") in ("1", "true", "yes"),
                week=query.get("week") in ("1", "true", "yes"),
                limit=self._int(query.get("limit"), "limit", 50, hi=Api.MAX_LIST_LIMIT),
            )
            return {"count": len(rows), "sessions": [r.to_dict() for r in rows]}

    def sessions_minutes(self, query: dict) -> dict:
        """Aggregated per-task minutes (active project by default): the kanban
        time badges need totals, not hundreds of session rows on every poll."""
        with self._conn() as c:
            pid = self._list_pid(c, query)
            if pid is None:
                rows = c.execute(
                    "SELECT task_id, COALESCE(SUM(duration_min), 0) AS m FROM sessions "
                    "WHERE task_id IS NOT NULL GROUP BY task_id"
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT task_id, COALESCE(SUM(duration_min), 0) AS m FROM sessions "
                    "WHERE project_id = ? AND task_id IS NOT NULL GROUP BY task_id",
                    (pid,),
                ).fetchall()
            return {"minutes": {r["task_id"]: r["m"] for r in rows}}

    # --- status / events (realtime) ----------------------------------------

    def status(self) -> dict:
        """Snapshot for the GUI header: active project, open tasks, running session."""
        with self._conn() as c:
            active = proj.get_active(c)
            if active is None:
                return {"active": None, "session": None, "last_event_id": ev.last_id(c)}
            open_tasks = tsk.list(c, project_id=active.id, status=["todo", "in_progress"])
            in_progress = [t.to_dict() for t in open_tasks if t.status == "in_progress"]
            s = sess.current(c)
            if s is not None and s.project_id != active.id:
                s = None
            return {
                "active": active.to_dict(),
                "open_tasks": len(open_tasks),
                "in_progress": in_progress,
                "done_today": stats.tasks_done_today(c, active.id),
                "session": None if s is None else {
                    **s.to_dict(), "elapsed_min": sess.elapsed_min(s),
                },
                "today_minutes": stats.today_minutes(c, active.id),
                "streak_days": stats.streak_days(c, active.id),
                "progress": stats.project_progress(c, active.id),
                "git": gitwatch.status(c, active.id),
                "last_event_id": ev.last_id(c),
            }

    def events_since(self, since) -> dict:
        with self._conn() as c:
            since_id = self._int(since, "since", 0)
            if since_id < 0:
                raise ApiError("invalid since: must be >= 0")
            got = ev.since(c, since_id)
            # last_id must be the last event actually RETURNED: with the LIMIT
            # in ev.since, the global max would silently skip the missing ones
            last = got[-1].id if got else since_id
            return {"events": [e.to_dict() for e in got], "last_id": last}
