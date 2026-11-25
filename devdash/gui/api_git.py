# git endpoints of the GUI REST layer (mixin of Api).
from __future__ import annotations

from .api_common import ApiError
from devdash.core import config
from devdash.core import gitwatch
from devdash.core import sysopen

class GitMixin:
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
                limit=self._int(query.get("limit"), "limit", 100, hi=self.MAX_LIST_LIMIT),
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


    def _with_linked_repo(self, body: dict, fn) -> dict:
        """Resolve active project + linked repo (clean 400 if none), then call
        fn(conn, project, repo) inside the request connection."""
        with self._conn() as c:
            p = self._resolve_project(c, body.get("project"))
            repo = gitwatch.get_link(c, p.id)
            if not repo:
                raise ApiError(f"project {p.slug} has no linked repo", status=400)
            return fn(c, p, repo)

    def git_run(self, body: dict) -> dict:
        """Run a user-typed git command in the linked repo of the active project.
        READ-ONLY by construction: shlex argv (no shell) + subcommand allowlist +
        forbidden-option denylist, confined to the linked repo, with timeout.
        A rejected command returns 200 with ok=false (like a real terminal)."""
        cmd = self._str(body, "cmd", required=True)
        def _go(c, p, repo):
            r = gitwatch.run_readonly(repo, cmd)
            r["project"] = p.slug
            r["repo"] = repo
            return r
        return self._with_linked_repo(body, _go)


    def git_remote(self, body: dict) -> dict:
        """Remote READ on the linked repo: fetch / pull --ff-only / remote -v.
        Remote writes are refused by construction (run_remote_read allowlist)."""
        cmd = self._str(body, "cmd", required=True).strip()
        def _go(c, p, repo):
            r = gitwatch.run_remote_read(repo, cmd)
            r["project"] = p.slug
            r["repo"] = repo
            return r
        return self._with_linked_repo(body, _go)


    def git_diff(self, body: dict) -> dict:
        """Combined READ-ONLY diff of selected changed files (paths validated and
        confined to the linked repo, passed after '--')."""
        files = body.get("files")
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            raise ApiError("files must be a list of paths")
        staged = bool(body.get("staged"))

        def _go(c, p, repo):
            r = gitwatch.diff_files(repo, files, staged=staged)
            r["project"] = p.slug
            return r
        return self._with_linked_repo(body, _go)


    def git_changes(self, body: dict) -> dict:
        """GitHub-Desktop-style 'Changes': working-tree files + +/- counts.
        Read-only (status --porcelain + diff --numstat via the allowlisted runner).
        Also reports `linked_task`: the task these uncommitted changes most likely
        belong to — the running session's task first, else the last commit's parsed
        task. The GUI uses it to offer/ask completion of that task."""
        def _go(c, p, repo):
            r = gitwatch.changes(repo)
            r["project"] = p.slug
            r["repo"] = repo
            r["linked_task"] = self._linked_task_for_changes(c, p.id)
            return r
        return self._with_linked_repo(body, _go)


    def git_branches(self, body: dict) -> dict:
        """Local branches with tip commits, current flagged. Read-only, no checkout."""
        repo, p = self._linked_repo(body)
        r = gitwatch.branches(repo)
        r["project"] = p.slug
        r["repo"] = repo
        return r


    def git_open(self, body: dict) -> dict:
        """Open the LINKED repo folder in the OS file explorer.
        Confinement: only the repo path stored server-side is ever opened,
        argv list (no shell). Explicit user click only."""
        repo, p = self._linked_repo(body)
        try:
            sysopen.open_in_explorer(repo)
        except OSError as e:
            raise ApiError(f"could not open the file explorer: {e}")
        return {"opened": repo, "project": p.slug}


