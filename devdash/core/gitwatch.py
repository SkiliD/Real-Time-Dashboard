"""parse the git log read-only, link commits to tasks

Small on purpose -- it has to stay fast.
"""

from __future__ import annotations

import os
import re
import shlex
import sqlite3
import subprocess
from pathlib import Path

from datetime import datetime, timezone

from . import config, events, i18n, projects
from .models import GitCommit, day_start_utc, now_iso

MAX_COMMITS_PER_SYNC = 1000
MAX_LIST_ROWS = 100_000  # hard cap for list-style queries (sessions.list has its own)


def _to_utc(iso_with_offset: str) -> str:
    """Normalize an ISO-8601 timestamp (e.g. git %cI with +02:00) to UTC +00:00,
    so lexical string comparison/sorting in SQL is chronologically correct."""
    try:
        return datetime.fromisoformat(iso_with_offset).astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return iso_with_offset


class GitError(Exception):
    """Raised when a git operation fails (not a repo, git missing, ...)."""


def _git_env(extra: dict | None = None) -> dict:
    """Environment for git subprocesses: NEVER inherit GIT_* variables
    (GIT_EXTERNAL_DIFF / GIT_CONFIG_PARAMETERS / GIT_SSH_COMMAND could spawn
    processes or inject config) — set only the ones we need. `extra` is merged
    last (used to cap allowed URL protocols on remote operations)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # NOTE: no GIT_CONFIG_NOSYSTEM here — the machine's own system/global config is
    # trusted (it is what makes core.autocrlf work on Windows); only the *repo*
    # config is untrusted, and --no-textconv/--no-ext-diff neutralize its drivers.
    env.update({"GIT_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"})
    if extra:
        env.update(extra)
    return env


def _git(repo_path, *args: str, timeout: int = 30) -> str:
    """Run a read-only git command in repo_path and return stdout."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",  # git emits UTF-8; locale (cp1252) would mojibake it
            stdin=subprocess.DEVNULL, env=_git_env(),
        )
    except FileNotFoundError as e:
        raise GitError("git executable not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {' '.join(args)} timed out") from e
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


# --- read-only git command runner (GUI terminal box, CLI `git run`, MCP) -------
# The app NEVER runs arbitrary git: a typed line is shlex-split into argv (no shell,
# so ; | && > are inert), the subcommand must be in this allowlist, and any option
# that could escape the repo, write a file or spawn a process is refused.
READONLY_GIT_CMDS = frozenset({
    "status", "log", "diff", "show", "branch", "tag", "ls-files", "shortlog",
    "blame", "describe", "rev-parse", "name-rev", "cat-file", "grep",
    "count-objects", "verify-tag", "fsck", "reflog", "remote",
})
FORBIDDEN_GIT_OPTS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--exec-path", "--namespace",
    "-o", "--output", "--exec",                      # path escape / file write / process spawn
    "-d", "-D", "-m", "-M", "-f", "--force",         # branch/tag delete/move/force
    "--delete", "--move", "--remove", "--unset", "--add", "--replace-all",
    "--no-index",                                    # diff/grep outside the worktree -> file read anywhere
})
#: long options refused by PREFIX, so "--opt=value" and "--optvalue" forms are caught too
FORBIDDEN_GIT_LONG_PREFIXES = (
    "--git-dir", "--work-tree", "--exec-path", "--exec", "--output", "--namespace",
    "--no-index", "--force", "--delete", "--move", "--remove", "--unset", "--add",
    "--replace-all",
    # process-spawning / clone / diff-filter vectors (hostile repo or URL remote):
    "--ext-diff", "--textconv", "--upload-pack", "--recurse-submodules",
    "--depth", "--shallow-since", "--shallow-exclude", "--negotiation-tip", "--filter",
    "--lost-found",  # fsck --lost-found WRITES dangling objects into .git/lost-found
)
#: letters that must never appear in a short-option cluster (attached forms like -dfoo)
FORBIDDEN_GIT_SHORT_LETTERS = frozenset("dDmMfcoC")
MAX_CMD_CHARS = 500
MAX_OUT_CHARS = 50_000  # terminal box is a text node: 200k was heavy on big `git log`


def _refused_git_arg(a: str) -> str | None:
    """Refusal reason for one git argument, or None if acceptable. Fail closed:
    unknown LONG options pass (listing flags like --oneline), but any long option
    prefixed like a write/escape option is refused, short clusters containing a
    write letter (-dfoo, -mfoo, -Cpath...) are refused, and absolute paths are
    refused so the command stays confined to the linked repo."""
    if a == "--":
        return None  # path separator: the args after it are checked as paths
    if a in FORBIDDEN_GIT_OPTS:
        return f"option {a!r} is not allowed in read-only mode"
    if a.startswith("--"):
        for p in FORBIDDEN_GIT_LONG_PREFIXES:
            if a.startswith(p):
                return f"option {a!r} is not allowed in read-only mode"
        return None
    if a.startswith("-"):
        bad = FORBIDDEN_GIT_SHORT_LETTERS.intersection(a[1:])
        if bad:
            return (f"option {a!r} is not allowed in read-only mode "
                    f"(write letter {''.join(sorted(bad))!r})")
        return None
    # positional argument: absolute paths would escape the repo confinement
    if a.startswith(("/", "\\")) or (len(a) > 1 and a[1] == ":"):
        return f"absolute path {a!r} is not allowed (confined to the linked repo)"
    return None


def _run_git(repo_path, argv: list[str], timeout: int, display: str | None = None,
             extra_env: dict | None = None) -> dict:
    """Run git with an argv list (NEVER a shell string) confined to repo_path via
    our own -C. Returns {"ok", "returncode", "stdout", "stderr", "cmd"} — runtime
    failures are values in the dict, never exceptions."""
    out = {"ok": False, "returncode": None, "stdout": "", "stderr": "",
           "cmd": display if display is not None else " ".join(argv)}
    argv = list(argv)
    # Diff-rendering subcommands must never apply textconv/external-diff filters:
    # a hostile LINKED repo can ship a .gitattributes/.git/config whose driver
    # executes a command at the first `log -p`/`show`/`diff`. The flags go right
    # after the subcommand (before any `--` path separator) so they always apply.
    if argv and argv[0] in ("log", "show", "diff"):
        argv = [argv[0], "--no-textconv", "--no-ext-diff", *argv[1:]]
    try:
        proc = subprocess.run(
            ["git", "--no-pager", "-C", str(repo_path), *argv],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",  # git emits UTF-8; locale (cp1252) would mojibake it
            stdin=subprocess.DEVNULL,  # shortlog / cat-file --batch would block on stdin
            env=_git_env(extra_env),
        )
    except FileNotFoundError:
        out["stderr"] = "git executable not found on PATH"
        return out
    except subprocess.TimeoutExpired:
        out["stderr"] = f"git {' '.join(argv)} timed out after {timeout}s"
        return out
    out.update(ok=proc.returncode == 0, returncode=proc.returncode,
               stdout=proc.stdout[:MAX_OUT_CHARS], stderr=proc.stderr[:MAX_OUT_CHARS])
    return out


def run_readonly(repo_path, cmdline: str, timeout: int = 10) -> dict:
    """Run a user-typed git command restricted to READ-ONLY operations confined to
    repo_path. Returns {"ok", "returncode", "stdout", "stderr", "cmd"} — bad input
    comes back as ok=false with an explanation, never as an exception."""
    out = {"ok": False, "returncode": None, "stdout": "", "stderr": "", "cmd": cmdline}
    if not isinstance(cmdline, str) or not cmdline.strip():
        out["stderr"] = "empty command"
        return out
    if len(cmdline) > MAX_CMD_CHARS:
        out["stderr"] = f"command too long (max {MAX_CMD_CHARS} chars)"
        return out
    try:
        argv = shlex.split(cmdline)
    except ValueError as e:
        out["stderr"] = f"could not parse command: {e}"
        return out
    if argv and argv[0] == "git":
        argv = argv[1:]
    if not argv:
        out["stderr"] = "empty command"
        return out
    sub = argv[0]
    if sub not in READONLY_GIT_CMDS:
        out["stderr"] = (f"git '{sub}' is not allowed — read-only commands only: "
                         + ", ".join(sorted(READONLY_GIT_CMDS)))
        return out
    for a in argv[1:]:
        reason = _refused_git_arg(a)
        if reason:
            out["stderr"] = reason
            return out
    # `git branch foo` / `git tag v1` CREATE refs: positional args only for listing
    if sub in ("branch", "tag") and any(a and not a.startswith("-") for a in argv[1:]):
        out["stderr"] = f"git {sub}: positional arguments are not allowed (would create a ref)"
        return out
    # `git reflog delete/expire` are writes even though `reflog show` is read-only
    if sub == "reflog" and any(a in ("delete", "expire") or a.startswith("-") for a in argv[1:]):
        out["stderr"] = "git reflog: only 'reflog show' is allowed (delete/expire are writes)"
        return out
    if sub == "remote" and any(a not in ("-v", "--verbose") for a in argv[1:]):
        out["stderr"] = "git remote: only '-v' is allowed (read-only)"
        return out
    return _run_git(repo_path, argv, timeout, cmdline)


# ---------------------------------------------------------------------------
#: remote READ commands the project rules allow (AGENTS.md lists fetch,
#: pull --ff-only, remote -v). Remote WRITES are forbidden under any form and
#: are deliberately absent from every allowlist in this app.
REMOTE_READ_CMDS = frozenset({"fetch", "pull", "remote"})
REMOTE_TIMEOUT = 30


def run_remote_read(repo_path, cmdline: str, timeout: int = REMOTE_TIMEOUT) -> dict:
    """Fetch / pull (--ff-only ONLY) / remote -v on a repo — same confinement as
    run_readonly (shlex argv, no shell, our own -C, no credentials on terminal),
    plus: pull is refused without --ff-only, fetch takes no options."""
    out = {"ok": False, "returncode": None, "stdout": "", "stderr": "", "cmd": cmdline}
    if not isinstance(cmdline, str) or not cmdline.strip():
        out["stderr"] = "empty command"
        return out
    if len(cmdline) > MAX_CMD_CHARS:
        out["stderr"] = f"command too long (max {MAX_CMD_CHARS} chars)"
        return out
    try:
        argv = shlex.split(cmdline)
    except ValueError as e:
        out["stderr"] = f"could not parse command: {e}"
        return out
    if argv and argv[0] == "git":
        argv = argv[1:]
    if not argv:
        out["stderr"] = "empty command"
        return out
    sub = argv[0]
    if sub not in REMOTE_READ_CMDS:
        out["stderr"] = (f"git '{sub}' is not allowed here — allowed: "
                         + ", ".join(sorted(REMOTE_READ_CMDS))
                         + " (pull only with --ff-only; remote writes are forbidden by project rules)")
        return out
    for a in argv[1:]:
        reason = _refused_git_arg(a)
        if reason:
            out["stderr"] = reason
            return out
    if sub in ("fetch", "pull"):
        # A remote name or branch name never contains ':'. Refusing ':' blocks
        # URL remotes (ssh://, https://), ext::/fd:: transports (shell exec via
        # git-remote-ext) AND refspecs like +refs/heads/a:refs/heads/b (ref writes).
        for a in argv[1:]:
            if not a.startswith("-") and ":" in a:
                out["stderr"] = (f"remote/refspec {a!r} refused: only a configured remote name and a "
                                 "plain branch name are allowed (no ':', no URL, no ext:: transport)")
                return out
    if sub == "pull":
        opts = [a for a in argv[1:] if a.startswith("-") and a != "--"]
        if "--ff-only" not in opts:
            out["stderr"] = "git pull is only allowed with --ff-only (project rule: never force/merge/rebase)"
            return out
        extra = [a for a in opts if a != "--ff-only"]
        if extra:
            out["stderr"] = (f"pull option(s) refused: {', '.join(extra)} "
                             "(only --ff-only is allowed; --upload-pack/--recurse-submodules/... would spawn processes)")
            return out
    if sub == "fetch" and any(a.startswith("-") for a in argv[1:]):
        out["stderr"] = "git fetch: options are not allowed (bare 'fetch [remote]' only)"
        return out
    if sub == "remote" and any(a not in ("-v", "--verbose") for a in argv[1:]):
        out["stderr"] = "git remote: only '-v' is allowed"
        return out
    # A hostile LINKED repo can point remote.origin.url at ext::sh -c '…' (git-remote-ext
    # executes it on fetch/pull) or use fd:: transports. Kill both. The -c flags are
    # GLOBAL git options: they must precede the subcommand (git -c x=y <sub>).
    # NOTE: no GIT_ALLOW_PROTOCOL env — setting it makes even the local "file"
    # transport fail ("transport 'file' not allowed"), breaking local-path remotes.
    argv = ["-c", "protocol.ext.allow=never", "-c", "protocol.fd.allow=never", sub, *argv[1:]]
    return _run_git(repo_path, argv, timeout, cmdline)


MAX_DIFF_FILES = 200
MAX_PATH_CHARS = 4096


def valid_repo_relpath(p) -> bool:
    """A path used in a git command must stay INSIDE the repo: relative, no '..'
    segment, not absolute, no drive letter."""
    if not isinstance(p, str) or not p or len(p) > MAX_PATH_CHARS:
        return False
    if p.startswith(("/", "\\")) or (len(p) > 1 and p[1] == ":"):
        return False
    parts = p.replace("\\", "/").split("/")
    return all(part not in ("", ".", "..") for part in parts)


def diff_files(repo_path, files, staged: bool = False, timeout: int = 10) -> dict:
    """Combined READ-ONLY diff of the selected files (working tree or index).
    Paths are validated (confined to the repo) and passed after '--' so they can
    never be reinterpreted as options."""
    if not isinstance(files, list) or not files or len(files) > MAX_DIFF_FILES:
        return {"ok": False, "returncode": None, "stdout": "",
                "stderr": f"files must be a non-empty list (max {MAX_DIFF_FILES})", "cmd": "diff"}
    bad = [f for f in files if not valid_repo_relpath(f)]
    if bad:
        return {"ok": False, "returncode": None, "stdout": "",
                "stderr": f"invalid path(s) refused: {bad[:3]}", "cmd": "diff"}
    argv = ["diff"] + (["--cached"] if staged else []) + ["--"] + [str(f) for f in files]
    return _run_git(repo_path, argv, timeout,
                    "diff " + ("--cached " if staged else "") + " ".join(str(f) for f in files))


# ---------------------------------------------------------------------------
# GitHub-Desktop-style read-only views: changed files, branches
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

#: porcelain status letter -> (css-ish kind, label key handled by the GUI)
PORCELAIN_KINDS = {
    "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
    "C": "copied", "T": "typechange", "U": "unmerged", "?": "untracked",
}


def _unquote(p: str) -> str:
    """Decode a git-quoted path. Git wraps exotic paths in double quotes and
    escapes special bytes as C-style sequences: \\, \", \n, \t and OCTAL \\NNN
    (raw filename bytes — the old `unicode_escape` decode treated them as
    latin-1 and produced mojibake). Octal bytes are collected as bytes and
    decoded as UTF-8, which is what git stores on disk."""
    if len(p) >= 2 and p.startswith('"') and p.endswith('"'):
        body = p[1:-1]
        out = bytearray()
        simple = {"n": b"\n", "t": b"\t", "r": b"\r", '"': b'"', "\\": b"\\"}
        i = 0
        while i < len(body):
            c = body[i]
            if c == "\\" and i + 1 < len(body):
                n = body[i + 1]
                if n in simple:
                    out += simple[n]
                    i += 2
                    continue
                if body[i + 1:i + 4].isdigit() and len(body[i + 1:i + 4]) == 3:
                    out.append(int(body[i + 1:i + 4], 8) & 0xFF)
                    i += 4
                    continue
                out += b"\\"
                i += 1
                continue
            out += c.encode("utf-8")
            i += 1
        return out.decode("utf-8", errors="replace")
    return p


def _split_rename(field: str) -> str:
    """porcelain rename/copy field 'ORIG -> NEW' (either side may be quoted)
    -> keep NEW. Quoted source: find the real end of the quoted string first;
    unquoted: split on the LAST ' -> ' (a destination may itself contain it)."""
    if field.startswith('"'):
        i = 1
        while i < len(field):
            if field[i] == "\\":
                i += 2
                continue
            if field[i] == '"':
                break
            i += 1
        rest = field[i + 1:]
        if rest.startswith(" -> "):
            return rest[4:]
        return field  # malformed: keep as-is
    if " -> " in field:
        return field.rsplit(" -> ", 1)[1]
    return field


def parse_porcelain(text: str) -> list[dict]:
    """Parse `git status --porcelain` output into structured rows.
    Handles the quoted paths ("..." with C/octal escapes) git emits for
    special chars (unicode, spaces, quotes...)."""
    rows = []
    for line in (text or "").splitlines():
        if len(line) < 4:
            continue
        x, y = line[0], line[1]
        path = _split_rename(line[3:])
        path = _unquote(path)
        rows.append({"path": path, "x": x, "y": y, "staged": x not in ("?", " "),
                     "kind": PORCELAIN_KINDS.get(x if x != " " else y, "modified")})
    return rows


def parse_numstat(text: str) -> dict[str, tuple[int, int]]:
    """Parse `git diff --numstat` output: {path: (added, deleted)}.
    Binary files show '-' for the counts; paths are unquoted like porcelain."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted, path = parts[0], parts[1], "\t".join(parts[2:])
        path = _unquote(path)
        try:
            out[path] = (int(added), int(deleted))
        except ValueError:  # binary file: '-' counts
            out[path] = (0, 0)
    return out


def changes(repo_path) -> dict:
    """GitHub Desktop 'Changes' view, read-only: working-tree files with their
    status letter and +/- line counts (staged and unstaged diffs SUMMED — a
    file modified in both index and worktree shows the total)."""
    st = run_readonly(repo_path, "status --porcelain")
    if not st["ok"]:
        return {"ok": False, "error": st["stderr"], "files": []}
    num = run_readonly(repo_path, "diff --numstat")          # unstaged
    numc = run_readonly(repo_path, "diff --cached --numstat")  # staged
    adds: dict[str, tuple[int, int]] = {}
    for src in (parse_numstat(num["stdout"]), parse_numstat(numc["stdout"])):
        for k, (a, d) in src.items():
            pa, pd = adds.get(k, (0, 0))
            adds[k] = (pa + a, pd + d)
    files = parse_porcelain(st["stdout"])
    for f in files:
        f["added"], f["deleted"] = adds.get(f["path"], (0, 0))
    return {"ok": True, "files": files,
            "count": len(files),
            "insertions": sum(f["added"] for f in files),
            "deletions": sum(f["deleted"] for f in files)}


def branches(repo_path) -> dict:
    """Local branches with their tip commit (read-only `git branch -v`),
    current branch first and flagged. No checkout — display only."""
    r = run_readonly(repo_path, "branch -v --no-abbrev")
    if not r["ok"]:
        return {"ok": False, "error": r["stderr"], "branches": [], "current": None}
    out, current = [], None
    for line in r["stdout"].splitlines():
        line = line.rstrip()
        if not line:
            continue
        is_cur = line.startswith("*")
        body = line[2:] if line[:1] in ("*", "+") else line
        if body.startswith("(HEAD detached"):
            # "* (HEAD detached at abc123) <hash> <subject>" — the pseudo-branch
            # name contains spaces, so it needs its own parsing
            close = body.find(")")
            name = "(HEAD detached)"  # clean label; the hash stays in `short`
            rest = (body[close + 1:] if close != -1 else "").split(None, 1)
            short = rest[0] if rest else ""
            subject = rest[1] if len(rest) > 1 else ""
        else:
            parts = body.split(None, 2)
            if len(parts) < 2:
                continue
            name, short = parts[0], parts[1]
            subject = parts[2] if len(parts) > 2 else ""
        out.append({"name": name, "current": is_cur, "short": short, "subject": subject})
        if is_cur:
            current = name
    out.sort(key=lambda b: (not b["current"], b["name"]))
    return {"ok": True, "branches": out, "current": current}


def is_repo(path) -> bool:
    try:
        return _git(path, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False


# ---------------------------------------------------------------------------
# .gitignore management: DevDash must never pollute the USER's project repo.
# The block is marker-delimited so re-running replaces it instead of duplicating.
# ---------------------------------------------------------------------------

IGNORE_BEGIN = "# >>> devdash >>>"
IGNORE_END = "# <<< devdash <<<"

#: lines always ignored (DevDash data/artifacts, wherever they land in the repo)
IGNORE_LINES = [
    ".devdash/",
    "devdash.db",
    "devdash.db-shm",
    "devdash.db-wal",
    ".devdash-snapshot-*.db",
    "devdash-backup-*.zip",
    "devdash-export-*.json",
]


def gitignore_block(home: Path | None = None, repo: Path | None = None) -> list[str]:
    """The marker-delimited .gitignore block. If DEVDASH_HOME lives inside the
    repo, its config.json (API keys!) is ignored by its repo-relative path."""
    lines = list(IGNORE_LINES)
    if home is not None and repo is not None:
        try:
            rel = (home.resolve() / "config.json").relative_to(repo.resolve())
            lines.append(str(rel).replace("\\", "/"))
        except (ValueError, OSError):
            pass  # home outside the repo: nothing extra to ignore
    return [IGNORE_BEGIN, "# DevDash local data — never commit these"] + lines + [IGNORE_END]


def ensure_gitignore(repo_path: str | Path, home: Path | None = None) -> dict:
    """Idempotently add/refresh the DevDash block in <repo>/.gitignore.

    This is the ONLY file DevDash ever writes inside a user's repository, and
    only on explicit request (`devdash git ignore` / GUI button) — it touches
    no git history (read-only rule preserved). Returns what happened."""
    repo = Path(repo_path).expanduser().resolve()
    if not is_repo(repo):
        raise GitError(f"not a git repository: {repo}")
    gi = repo / ".gitignore"
    # never write THROUGH a symlink (a cloned hostile repo could plant one):
    # refuse instead of following the link outside the repo
    if gi.is_symlink():
        raise GitError(f"refusing to write through a symlink: {gi}")
    if gi.exists() and not gi.is_file():
        raise GitError(f".gitignore exists and is not a regular file: {gi}")
    block = gitignore_block(home=home, repo=repo)
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    # drop any previous marked block (regex with DOTALL between the markers)
    cleaned = re.sub(
        re.escape(IGNORE_BEGIN) + r".*?" + re.escape(IGNORE_END) + r"\n?",
        "", text, flags=re.DOTALL,
    ).rstrip("\n")
    new_text = (cleaned + "\n\n" if cleaned else "") + "\n".join(block) + "\n"
    if gi.exists() and text == new_text:
        return {"updated": False, "path": str(gi), "lines": len(block),
                "already_present": True}
    # O_NOFOLLOW: the open itself fails on a symlink, so the is_symlink() check
    # above cannot be raced (TOCTOU) into writing through a planted link
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(gi, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return {"updated": True, "path": str(gi), "lines": len(block)}


# --- links (project <-> local repo) ----------------------------------------

def link(conn: sqlite3.Connection, project_id: int, repo_path: str) -> str:
    if projects.get(conn, project_id) is None:
        raise KeyError(i18n.tr("err.no_project_id", id=project_id))
    p = Path(repo_path).expanduser().resolve()
    if not is_repo(p):
        raise GitError(f"not a git repository: {p}")
    with conn:
        conn.execute(
            "INSERT INTO git_links(project_id, repo_path, added_at) VALUES (?, ?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET repo_path = excluded.repo_path",
            (project_id, str(p), now_iso()),
        )
        events.append(conn, "git", project_id, "linked", {"repo_path": str(p)}, commit=False)
    return str(p)


def unlink(conn: sqlite3.Connection, project_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM git_links WHERE project_id = ?", (project_id,))
        events.append(conn, "git", project_id, "unlinked", {}, commit=False)


def get_link(conn: sqlite3.Connection, project_id: int) -> str | None:
    row = conn.execute(
        "SELECT repo_path FROM git_links WHERE project_id = ?", (project_id,)
    ).fetchone()
    return row["repo_path"] if row else None


def linked_repos(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    rows = conn.execute("SELECT project_id, repo_path FROM git_links").fetchall()
    return [(r["project_id"], r["repo_path"]) for r in rows]


# --- commit parsing ----------------------------------------------------------

# Commit subjects are matched against USER-CONFIGURED regexes (config.json):
# cap the matched text so a pathological pattern cannot cause ReDoS on a huge
# commit message (a subject is one line; 500 chars is already generous).
MAX_PARSE_INPUT_CHARS = 500


def parse_message(message: str, patterns: list[str]) -> tuple[int | None, str | None]:
    """Return (task_id, feature) parsed from a commit message, or (None, None).

    First matching pattern wins. A numeric capture group -> task id; otherwise
    the capture is treated as a feature name.
    """
    from .config import _has_nested_quantifier  # local import: avoid a cycle at module load
    haystack = message[:MAX_PARSE_INPUT_CHARS]
    for pattern in patterns:
        if _has_nested_quantifier(pattern):
            # hand-edited config.json bypassed validation: refuse rather than hang
            raise GitError(f"commit pattern {pattern!r} rejected: nested quantifiers (ReDoS risk)")
        try:
            m = re.search(pattern, haystack, re.IGNORECASE)
        except re.error as e:
            raise GitError(f"invalid commit pattern {pattern!r}: {e}") from e
        if m and m.groups():
            value = m.group(1)
            if value.isdigit():
                return int(value), None
            return None, value
    return None, None


# --- sync (read-only) --------------------------------------------------------

def _parse_log(raw: str, branch: str, repo_path: str, patterns: list[str]) -> list[dict]:
    """Split the `git log --format=%H%x1f%h%x1f%an%x1f%s%x1f%cI%x1e` output into
    commit dicts (\x1e between records, \x1f between fields). Records with a wrong
    field count are skipped; committed_at is normalized to UTC so lexicographic
    sorting matches chronological order."""
    records = []
    for record in raw.split("\x1e"):
        record = record.strip("\r\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) != 5:
            continue
        commit_hash, short, author, subject, committed_at = parts
        task_id, feature = parse_message(subject, patterns)
        records.append({
            "hash": commit_hash, "short": short, "message": subject, "author": author,
            "branch": branch, "committed_at": _to_utc(committed_at),
            "task_id": task_id, "feature": feature, "repo_path": repo_path,
        })
    return records


def sync_repo(conn: sqlite3.Connection, repo_path: str, patterns: list[str]) -> dict:
    """Read `git log` of repo_path and cache commits in git_commits.

    Emits one `git.commit` event per NEW commit. Idempotent (INSERT OR IGNORE).
    """
    p = Path(repo_path).expanduser().resolve()
    if not p.exists():
        raise GitError(f"path does not exist: {p}")
    # one subprocess answers everything: repo validity, HEAD hash and branch
    # (in %D, "HEAD -> main" means we are on main; plain "HEAD" = detached)
    out = _git(p, "show", "-s", "--format=%H%x1f%D", "HEAD").strip()
    head, _, refs = out.partition("\x1f")
    head = head.strip()
    branch = "HEAD"
    if "HEAD -> " in refs:
        branch = refs.split("HEAD -> ", 1)[1].split(",", 1)[0].strip() or "HEAD"

    # cheap check: if HEAD did not move AND the branch is unchanged since the
    # last sync, skip the full `git log` (a branch switch with the same HEAD
    # still triggers a sync so the recorded branch stays correct)
    state = conn.execute(
        "SELECT last_seen_hash, branch FROM git_sync_state WHERE repo_path = ?", (str(p),)
    ).fetchone()
    if (state and head and state["last_seen_hash"] == head
            and (state["branch"] or branch) == branch):
        cached = conn.execute(
            "SELECT COUNT(*) AS c FROM git_commits WHERE repo_path = ?", (str(p),)
        ).fetchone()["c"]
        return {"repo_path": str(p), "branch": state_branch(conn, str(p)) or branch,
                "new": 0, "total": cached, "skipped": True}

    raw = _git(
        p, "log", "--format=%H%x1f%h%x1f%an%x1f%s%x1f%cI%x1e", "-n", str(MAX_COMMITS_PER_SYNC)
    )
    new = 0
    total = 0
    pending_events = []
    # one transaction for all inserts + all events (instead of 2 fsyncs per
    # commit); on ANY failure roll back so a long-lived shared connection
    # (MCP server) never commits partial inserts later by accident
    try:
        for ev in _parse_log(raw, branch, str(p), patterns):
            total += 1
            cur = conn.execute(
                "INSERT OR IGNORE INTO git_commits(hash, repo_path, short, author, message, "
                "branch, committed_at, parsed_task_id, parsed_feature) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ev["hash"], ev["repo_path"], ev["short"], ev["author"], ev["message"],
                 ev["branch"], ev["committed_at"], ev["task_id"], ev["feature"]),
            )
            if cur.rowcount:  # new commit
                new += 1
                pending_events.append(ev)
        conn.execute(
            "INSERT INTO git_sync_state(repo_path, last_sync_at, last_seen_hash, branch) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(repo_path) DO UPDATE SET "
            "last_sync_at = excluded.last_sync_at, last_seen_hash = excluded.last_seen_hash, "
            "branch = excluded.branch",
            # store the FULL HEAD hash (not the short one) so the next sync's
            # "HEAD unchanged" cheap-skip actually triggers
            (str(p), now_iso(), head, branch),
        )
        for ev in pending_events:
            events.append(conn, "git", ev["hash"], "commit", ev, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"repo_path": str(p), "branch": branch, "new": new, "total": total}


def state_branch(conn: sqlite3.Connection, repo_path: str) -> str | None:
    """Branch recorded at the last sync, if any."""
    row = conn.execute(
        "SELECT branch FROM git_sync_state WHERE repo_path = ?", (repo_path,)
    ).fetchone()
    return row["branch"] if row else None


def sync_all(conn: sqlite3.Connection, home=None) -> dict:
    """Sync every linked repo. Never raises: per-repo errors are collected."""
    cfg = config.load_config(home)
    patterns = cfg.commit_patterns
    results, errors = [], []
    for project_id, repo_path in linked_repos(conn):
        try:
            results.append(sync_repo(conn, repo_path, patterns))
        except GitError as e:
            errors.append({"project_id": project_id, "repo_path": repo_path, "error": str(e)})
    return {"repos": results, "errors": errors}


# --- queries -----------------------------------------------------------------

def commits(
    conn: sqlite3.Connection,
    repo_path: str | None = None,
    project_id: int | None = None,
    task_id: int | None = None,
    unparsed: bool = False,
    limit: int = 100,
) -> list[GitCommit]:
    clauses, values = [], []
    if repo_path is not None:
        clauses.append("repo_path = ?")
        values.append(str(Path(repo_path).resolve()))
    if project_id is not None:
        clauses.append("repo_path IN (SELECT repo_path FROM git_links WHERE project_id = ?)")
        values.append(project_id)
    if task_id is not None:
        clauses.append("parsed_task_id = ?")
        values.append(task_id)
    if unparsed:
        clauses.append("parsed_task_id IS NULL AND parsed_feature IS NULL")
    limit = min(limit, MAX_LIST_ROWS)  # CLI --limit 999999999 must not materialize everything
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM git_commits {where} "
        # committed_at is normalized UTC ISO (+00:00): lexicographic = chronological,
        # and idx_commits_repo_date serves the sort without a temp b-tree
        "ORDER BY committed_at DESC, hash DESC LIMIT ?",
        (*values, limit),
    ).fetchall()
    return [GitCommit.from_row(r) for r in rows]



def status(conn: sqlite3.Connection, project_id: int) -> dict:
    """Git snapshot for a project: link, branch, last commit, today's count.

    Pure DB reads (no git subprocess except one rev-parse if never synced):
    the watcher thread already keeps git_commits / git_sync_state up to date.
    """
    repo = get_link(conn, project_id)
    if repo is None:
        return {"project_id": project_id, "linked": False}

    state = conn.execute(
        "SELECT last_sync_at, last_seen_hash, branch FROM git_sync_state WHERE repo_path = ?",
        (repo,),
    ).fetchone()
    branch = state["branch"] if state and state["branch"] else None
    if branch is None:
        try:  # never synced yet: one cheap subprocess to learn the branch
            branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() or "HEAD"
        except GitError as e:
            return {"project_id": project_id, "linked": True, "repo_path": repo, "error": str(e)}

    # last commit + today's count straight from the cache (no full table scan)
    last = conn.execute(
        "SELECT hash, short, author, message, branch, committed_at, parsed_task_id, "
        "parsed_feature FROM git_commits WHERE repo_path = ? "
        "ORDER BY committed_at DESC LIMIT 1",  # ISO UTC: lexical sort, indexed
        (repo,),
    ).fetchone()
    today_count = conn.execute(
        "SELECT COUNT(*) AS c FROM git_commits WHERE repo_path = ? "
        "AND committed_at >= ?",
        (repo, day_start_utc(0)),
    ).fetchone()["c"]
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM git_commits WHERE repo_path = ?", (repo,)
    ).fetchone()["c"]

    return {
        "project_id": project_id, "linked": True, "repo_path": repo,
        "branch": branch,
        "last_commit": dict(last) if last else None,
        "commits_today": today_count,
        "last_sync_at": state["last_sync_at"] if state else None,
        "cached_commits": total,
    }
