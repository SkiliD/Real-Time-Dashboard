# projects endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from devdash.core import gitwatch
from devdash.core import projects as proj
from devdash.core import sysopen
from devdash.core import tasks as tsk
from pathlib import Path

class ProjectsMixin:
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
                    path=body.get("path"),
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
                    path=info["path"],  # remember the connected folder (open in explorer)
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


    def projects_open(self, body: dict) -> dict:
        """Open the project's CONNECTED folder in the OS file explorer.
        Confinement: only the path stored server-side at connect/update time is
        ever opened; argv list, no shell; explicit user click only."""
        ref = body.get("ref") or body.get("project")
        with self._conn() as c:
            p = self._resolve_project(c, ref)
            if not p.path:
                raise ApiError(
                    f"project {p.slug} has no connected folder (use 'projects connect <path>')",
                    status=400)
            if not Path(p.path).is_dir():
                raise ApiError(f"project folder no longer exists: {p.path}", status=400)
            try:
                sysopen.open_in_explorer(p.path)
            except OSError as e:
                raise ApiError(f"could not open the file explorer: {e}")
            return {"opened": p.path, "project": p.slug}

    # --- tasks ------------------------------------------------------------


    def tags(self, query: dict) -> dict:
        """All distinct task tags (unfiltered) — feeds the GUI tag filter select,
        which must list every tag even while a tag filter is active."""
        with self._conn() as c:
            pid = self._list_pid(c, query)
            return {"tags": tsk.list_tags(c, project_id=pid)}

