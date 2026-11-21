# DevDash — detailed technical reference

> Long, technical version. For the short, readable one: [README.md](../README.md) (English) / [README.fr.md](../README.fr.md) (French).
>
> 🇫🇷 French version of this detailed reference: [README.detail.md](README.detail.md).

> **100 % local, portable dev dashboard** for a solo developer (game dev or any
> other kind of project). CLI + native desktop app on a **shared core**: an action
> done in the terminal shows up instantly in the GUI, and vice versa. SQLite
> storage in a single copyable folder. **Read-only** Git integration. **Optional**
> AI assistant.

- No cloud, no account, no remote server.
- Engine-agnostic: Unity, Unreal, Godot, web, or anything else.
- Multi-project: switching the active project never touches other projects' data.
- The GUI is a **real native window** (PyWebView / WebView2 on Windows), not a web
  app: no browser tab, controlled RAM, no internet required (Chart.js is vendored
  locally).

---

## 1. Installation

Prerequisite: **Python 3.11+** (tested with 3.13/3.15). No dependency for the core.

### Windows

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
pip install -e ".[gui,test]"     # CLI + native window (pywebview) + tests
```

### macOS / Linux

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[gui,test]"
```

No-venv variant (user install):

```bash
pipx install .          # or: pip install --user -e .
```

Without installing (from the repo): `python -m devdash --help` works directly
(with `PYTHONPATH` pointing at the project folder).

### `dd` alias (optional but recommended)

- **Windows** — create `dd.bat` in a folder on your `PATH`:
  ```bat
  @echo off
  py -3 -m devdash %*
  ```
  (or `devdash %*` if installed via pip/pipx)
- **macOS / Linux** — in `~/.bashrc` or `~/.zshrc`:
  ```bash
  alias dd='devdash'
  ```

### Standalone executable (no Python install needed)

PyInstaller builds a single double-clickable binary:

```bash
pip install -e ".[build]"            # adds pyinstaller
py -3 scripts/build_exe.py           # -> dist/devdash.exe (Windows) / dist/devdash (macOS, Linux)
py -3 scripts/build_exe.py --onedir  # folder build (faster startup, easier to whitelist with antivirus)
py -3 scripts/build_exe.py --no-gui  # CLI-only exe (smaller, no pywebview)
```

- **Build on the target OS** (a PyInstaller exe is not cross-platform).
- The executable still stores its data in `DEVDASH_HOME` (`~/.devdash`): the binary
  is **portable** (copy it to a USB stick), updating = replace the file, migrating =
  copy `~/.devdash`.
- GUI mode (`devdash gui`): on Windows, WebView2 is required (preinstalled on
  Windows 11; otherwise `devdash gui --browser` works everywhere).
- The executable uses the same entry point as `python -m devdash`; every command in
  this README works identically (`devdash.exe status`, `--json`, …).
- Antivirus false positives are possible with onefile mode (UPX is disabled in
  `devdash.spec`): prefer `--onedir` or sign the binary in a corporate setting.

## 2. Quick start

```bash
devdash demo seed                 # demo data to explore without a real project
devdash gui                       # launches the native desktop window
devdash status                    # snapshot of the active project
devdash projects list
```

Or with a real project:

```bash
devdash projects create "Aether Vale" --engine unity --status development --set-active
devdash git link "C:\path\to\my\repo"     # read-only link to the Git repo
devdash task add "Player dash" --priority high --tag gameplay --est 90
devdash session start --task 1            # interactive timer
devdash session stop --note "dash works, needs polish"
devdash log add "decision: 0.15s input buffer on the dash"
devdash status
```

## 3. Launching the GUI

```bash
devdash gui                 # native window (PyWebView, WebView2 on Windows)
devdash gui --browser       # fallback: opens http://127.0.0.1:8765 in the browser
devdash gui --port 9000     # custom port (default: config gui_port = 8765)
```

The GUI serves a local page (dark theme) from a small internal HTTP server bound
to `127.0.0.1` and works **offline**. Updates are real-time: the GUI polls the
SQLite event bus every 1.5 s and a Git watcher scans linked repos every
`git_watch_interval_sec` (20 s by default) — a new commit refreshes the dashboard
without restarting. Read-only endpoints also expose the tag list (`GET /api/tags`),
the linked Git repos (`GET /api/git/repos`) and the files in `DEVDASH_HOME`
(`GET /api/files`: backups/exports, never `config.json`), confined to the local
home directory.

Tabs: **Tasks** (kanban), **Devlog**, **Git**, **Features**, **Stats** (embedded
Chart.js charts), **AI Assistant** (only visible when AI is enabled), **Settings**
(always visible: AI provider local or cloud + connection test + enabled features,
but also interface language, Pomodoro, Git watcher interval, GUI port, commit
patterns, and a zip backup button).

## 4. CLI reference

Human-readable output by default; add `--json` (global option, before the command)
for machine-readable output.

| Command | Example | Description |
|---|---|---|
| `projects list` | `devdash projects list` | list, `*` = active |
| `projects create` | `devdash projects create "Aether Vale" --engine unity --status development --color #7c5cff --set-active` | `--engine`, `--status`, `--color`, `--description`, `--set-active` |
| `projects connect <path>` | `devdash projects connect "C:\games\aether-vale" --set-active` | **connect an existing folder**: engine auto-detected (`ProjectSettings/ProjectVersion.txt` → unity, `*.uproject` → unreal, `project.godot` → godot, `package.json` → web), name suggested from the folder, git root found by walking up the tree and **auto-linked read-only** (commits imported immediately); `--no-git` to skip, `--engine` to force |
| `projects set-active <ref>` | `devdash projects set-active aether-vale` | **lossless**: a simple pointer, other projects keep everything |
| `projects show/update/archive` | `devdash projects show aether-vale` | detail, edit, archive. **Hard delete**: available from the GUI (sidebar ✎ → Delete, two-click confirmation) via `POST /api/projects/delete` — full cascade (tasks, sessions, devlog, linked commits), the active project is reassigned |
| `task add` | `devdash task add "Dash" -p high -t gameplay -t combat --est 90` | `-p` project (default: active), `--parent` for subtasks |
| `task list` | `devdash task list -s todo --priority high --tag ui --feature dash` | filters: status/priority/tag/feature |
| `task start/done/cancel/reopen <id>` | `devdash task done 3` | status change |
| `task edit <id>` | `devdash task edit 3 --priority critical --tag vfx` | edit |
| `task delete <id>` | `devdash task delete 3 --yes` | permanent delete (subtasks cascade) |
| `session start` | `devdash session start --task 3 --kind focus --pomodoro` | interactive timer; `--background` to return the prompt, `--pomodoro` = after focus, offers to auto-start the break |
| `session stop` | `devdash session stop --note "done: X, blocked: Y"` | duration + end note |
| `session now` / `session list` | `devdash session list --week` | running session / history (`--today`, `--week`, `-t <task id>`) |
| `log add` | `devdash log add "idea: chained air dash" --tag idea` | devlog entry |
| `log recent` / `log search` | `devdash log search "cooldown"` | dated, searchable journal |
| `git link <path>` | `devdash git link "C:\repo"` | links the active project to a repo (read-only) |
| `git sync` | `devdash git sync` | imports new commits (all projects if `-p` omitted) |
| `git log` | `devdash git log --task 3` | commits linked to a task (`--unparsed` = unrecognized) |
| `git status` | `devdash git status` | branch, last commit, commits/day |
| `git ignore` | `devdash git ignore [--repo <path>]` | writes/refreshes the **marker-delimited block** `# >>> devdash >>>` in the linked repo's `.gitignore`: `.devdash/`, `devdash.db*`, snapshots, `devdash-backup-*.zip`, `devdash-export-*.json`, and `<home>/config.json` when `DEVDASH_HOME` lives inside the repo (API keys). Idempotent (never duplicated), existing content preserved. **The only file DevDash ever writes in your repo, and only on this explicit command** — git history stays untouched |
| `feature add/list/show` | `devdash feature add dash` ; `devdash feature list` ; `devdash feature show dash` | create/list/detail (progress, linked tasks) |
| `feature link/unlink` | `devdash feature link dash 3 4` ; `devdash feature unlink dash 3` | attach/detach tasks |
| `feature done/reopen` | `devdash feature done dash` ; `devdash feature reopen dash` | **manual** feature validation (only a human can close a feature) |
| `feature approve/reject` | `devdash feature reject dash --feedback "not tested enough"` | approve/reject an AI proposal (rejections are remembered) |
| `feature edit/delete` | `devdash feature edit dash --name air-dash` ; `devdash feature delete dash --yes` | rename (unique per project) / permanent delete (tasks are detached, never deleted); also editable from the GUI (✎/🗑 on the feature card) via `POST /api/features/update` and `POST /api/features/delete` |
| `status` | `devdash status` ; `devdash status --watch` | real-time snapshot (tasks, session, streak, git) |
| `stats today/week/month/streak/tags/all` | `devdash stats week` | time, tasks done, streak, per-tag breakdown |
| `ai context` | `devdash ai context` | **shows the exact context** that would be sent to the AI — nothing is sent, even with AI enabled |
| `ai ask` | `devdash ai ask "where do I stand?"` | chat (if AI enabled) |
| `ai summarize` | `devdash ai summarize --period week` | weekly summary |
| `ai plan` | `devdash ai plan "combat system" --apply` | breaks down into subtasks (`--apply` = the only possible write) |
| `ai verdict` | `devdash ai verdict dash` | AI validation proposal (never automatic) |
| `ai verify <id>` | `devdash ai verify 3 [--apply]` | AI verdict on a task (objective met?) — read-only, except explicit `--apply` |
| `demo seed` | `devdash demo seed` | demo data (`--force` if projects already exist) |
| `backup` | `devdash backup --out D:\backups` | **timestamped zip** of `~/.devdash` (db + config), consistent SQLite snapshot even with the GUI open |
| `export` | `devdash export --out export.json` | **full JSON export** (all tables) for archive/migration/inspection — read-only |
| `import` | `devdash import export.json [--force]` | restores a JSON export into the database (refused if the DB is not empty, unless `--force`) |
| `mcp` | `devdash mcp` | **MCP server** (stdio JSON-RPC) to connect DevDash to an external AI agent |
| `gui` | `devdash gui` | native desktop window |
| `version` | `devdash version` | version + git hash of the checkout |
| `update` | `devdash update [--check] [--no-reinstall]` · `update check` · `update status` | **self-update**: git install → `git pull --ff-only` (never forced, refused on a dirty tree) + `pip install -e .` to refresh deps; pip/pipx install → prints the right command; executable → rebuild. Returns `restart_needed`. GUI: "Update" card in Settings (`GET /api/update/status`, `POST /api/update/check`, `POST /api/update`) |

### Git commit conventions

Messages are linked to tasks/features through configurable regexes
(`commit_patterns` in `config.json`):

- `#42` → task 42
- `task-42: ...` → task 42
- `feat(dash): ...` → feature "dash"

Unrecognized commits are listed separately (`git log --unparsed`).
**No Git write operations**: DevDash only runs `git log` / `git rev-parse`.

### Auto-syncing commits (optional, with Vibe)

To have `devdash git sync` run automatically after your commits:

**With Vibe** — hook in `~/.vibe/hooks.toml`:

```toml
[[hooks]]
name = "devdash-git-sync"
on = "post_tool"
tools = ["re:bash", "re:process_start"]
command = "devdash git sync"
```

**With Claude Code** — `PostToolUse` hook in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "devdash git sync" }
        ]
      }
    ]
  }
}
```

This way, a commit made from any agent (Vibe, Claude Code, or you) shows up in
the DevDash GUI without restarting.

### Connecting DevDash to an AI agent via MCP (Model Context Protocol)

`devdash mcp` starts a **stdlib MCP server** (JSON-RPC over stdio, no extra
package) that exposes DevDash to external agents — reads plus safe actions (add
task/log, session, stats, read-only git, …):

**Claude Code** — `.mcp.json` at the project root:
```json
{ "mcpServers": { "devdash": { "command": "devdash", "args": ["mcp"] } } }
```
(with the standalone executable: `"command": "D:\\tools\\devdash.exe"`)

**Vibe** — `~/.vibe/config.toml` (or the project's `.vibe/`):
```toml
[mcp_servers.devdash]
command = "devdash"
args = ["mcp"]
```

Exposed tools: `devdash_status`, `devdash_projects_list`, `devdash_tasks_list`,
`devdash_task_add`, `devdash_task_set_status`, `devdash_session_start/stop`,
`devdash_log_add/search`, `devdash_stats`, `devdash_git_status/log`,
`devdash_features_list`, `devdash_ai_ask` (if AI is enabled).
Same rule everywhere: **no destructive action**, read-only git, and a feature is
**never** marked done by the agent.

## 5. Data, portability, backup

All data lives in **a single folder**:

```
~/.devdash/                  # DEVDASH_HOME (overridable via env var)
├── devdash.db               #   all data (SQLite, WAL)
└── config.json              #   settings + API keys (chmod 600, NEVER in the db)
```

- **Backup** = copy the folder. **Duplication** = copy the folder elsewhere.
- `DEVDASH_HOME` moves everything (USB stick, synced folder like OneDrive/Dropbox,
  another machine): `set DEVDASH_HOME=D:\usb\devdash` (Windows) or
  `export DEVDASH_HOME=~/usb/devdash` (macOS/Linux).
- The config (API keys) is **separate** from the database: backing up `devdash.db`
  never includes a secret.
- Several independent projects = several `DEVDASH_HOME`s, or one folder with
  several projects (switching the active project without losing progress).

### Data schema (summary, see `devdash/core/schema.sql`)

| Table | Contents |
|---|---|
| `meta` | key/value pairs (e.g. `active_project_id`) |
| `projects` | slug, name, description, engine, color, status, archived |
| `features` | name, status (`open/proposed/done/rejected`), source (manual/AI), confidence, reasoning, feedback, `validated_at` (set **only** by a human) |
| `tasks` | project, parent (subtasks), title, priority, estimate, status, tags (JSON), linked feature |
| `sessions` | project, task, kind (focus/break/custom), start/end, duration, note |
| `log_entries` | dated devlog, linked to a project/task, tags |
| `git_links` / `git_commits` / `git_sync_state` | linked repos, imported commits (read-only), parsed task/feature, branch |
| `events` | **append-only event bus**: every mutation writes a row here; the GUI polls `events?since=` for real-time updates |

### Security (summary)

- GUI server **bound to `127.0.0.1` only**, with `Host` **and** `Origin` checks
  (anti-CSRF / anti-DNS-rebinding), `Content-Type: application/json` required on
  POSTs, 1 MB body cap, `X-Content-Type-Options: nosniff`, generic 500 errors
  (details are logged server-side only).
- Static files served via `is_relative_to` (no directory traversal).
- `devdash.db` and `config.json` are `chmod 600` (best effort on Windows).
- Git is **strictly read-only** (`git log` / `git rev-parse`, subprocess in list
  form, never a shell). The `commit_patterns` regexes run on a truncated subject
  (anti-ReDoS).
- No API key in the database, events, logs, or displayed errors.
- Accepted residual risk: `git link` can read the history of any local repo (that
  is its job) — the GUI is protected against cross-site calls, but only link
  trusted repos.

## 6. AI assistant (optional)

Disabled by default (`ai_enabled = false`): the tool is **fully functional**
without AI, and everything AI-related disappears from both the CLI and the GUI
(only the **Settings** tab stays visible, to configure the AI).
Each function can be toggled individually via `ai_feature_flags`.

**From the GUI**: ⚙ Settings tab → pick a provider from the list (local or
cloud), fill in model / URL / API key / CLI command, tick the features to
enable, then **Test connection** (sends a plain "OK" prompt only, never project
context) and **Save**. The entered key is never sent back to the client
(write-only field: leave empty = keep the stored key).

**By hand**: `~/.devdash/config.json` (create it by hand; `chmod 600` applied automatically on
write):

```json
{
  "lang": "en",
  "ai_enabled": true,
  "ai_provider": "ollama",
  "ai_model": "qwen3:8b",
  "ai_base_url": "",
  "ai_api_key": "",
  "ai_cli_command": "",
  "ai_context_max_chars": 6000,
  "ai_feature_flags": { "chat": true, "summarize": true, "plan": true, "feature_verdict": true },
  "commit_patterns": ["#(\\d+)", "task[-_ ]?(\\d+)", "feat(?:ure)?\\(([^)]+)\\)"],
  "pomodoro_focus_min": 25,
  "pomodoro_break_min": 5,
  "git_watch_interval_sec": 20,
  "gui_port": 8765
}
```

### Available providers (`ai_provider`)

| Provider | Target | Cloud? | Required config |
|---|---|---|---|
| `ollama` | local Ollama (`http://localhost:11434` by default, override via `ai_base_url`) | no | `ai_model`, e.g. `"qwen3:8b"` |
| `local` | **any local OpenAI-compatible server**: LM Studio (`:1234` by default), llama.cpp server (`:8080`), vLLM, text-generation-webui… | no | `ai_model` (+ `ai_base_url` for exotic ports) — no key needed |
| `cli` | **any local CLI AI** (llama-cli, mistral.rs, `ollama run`…): the JSON prompt goes to stdin, the reply comes back on stdout, **no shell** | no | `ai_cli_command`, e.g. `"llama-cli -m model.gguf"` |
| `anthropic` | **Claude** (Anthropic API, `https://api.anthropic.com` by default) | **yes** | `ai_api_key` (`sk-ant-…`) + `ai_model`, e.g. `"claude-sonnet-4-5"` |
| `mistral` / `openai` | cloud (default URLs) | **yes** | `ai_api_key` + `ai_model` |
| `openai_compat` | any OpenAI-compatible cloud endpoint | yes, unless `ai_base_url` is loopback | `ai_base_url` + `ai_api_key` + `ai_model` |
| `mock` | deterministic network-free provider (tests/demo) | no | nothing |

- `ai_context_max_chars`: max size of the compact context sent to the model (default 6000).

- A cloud provider = a **permanent visual warning** in the GUI (banner) and a
  printed warning in the CLI: project context is sent to an external service.
  Detection is URL-based (loopback = local).
- **Context preview**: `devdash ai context` (CLI) or the "Show what is sent to the
  AI" button (GUI) shows **exactly** the compact summary sent to the model
  (`core/ai/context.py`, truncated to 6000 chars) — **without sending anything**,
  even with AI enabled.
- **Key protection**: `config.json` lives in `~/.devdash/` (outside the repo), is
  written with `chmod 600`, and the key **never** appears in the database, events,
  logs, or error messages (masked as `***`). If you point `DEVDASH_HOME` inside
  the repo, `.gitignore` excludes `.devdash/`, `config.json`, `*.db`, `*.gguf`,
  `*secret*` — no API key can be committed by accident (`git ls-files` stays clean).

### Language

The whole interface (CLI **and** GUI) is translated. One option in `config.json`:

```json
{ "lang": "en" }
```

- `"en"` or `"fr"` (default) — hot-reloadable: every CLI command re-reads the
  config, and the GUI re-reads it on load (endpoint `/api/i18n`).
- Only **displayed** text is translated. CLI commands, stored values (`todo`,
  `high`, `unity`…), `config.json` keys, and `--json` output stay in English and
  stable for your scripts.
- The full catalog lives in `devdash/core/i18n.py` (one dict per language, ~350
  keys): adding a language = adding a dict + an entry in `SUPPORTED_LANGS`.

Safety rules: the AI **never** performs a destructive or write action without
explicit confirmation (`--apply` in the CLI, a confirmation click in the GUI).
The context sent to the AI is a **compact summary** built by the core
(`core/ai/context.py`), not a dump of the database. A feature is **never** marked
done automatically: the system proposes, the human decides (manual `feature
done`, or `feature approve` on an AI proposal; a rejection is stored as feedback).

## 7. Architecture

```
devdash/
├── core/                 # business logic, stdlib only, testable on its own
│   ├── db.py             #   SQLite connection, WAL, schema migrations
│   ├── schema.sql        #   documented schema (v4)
│   ├── models.py         #   dataclasses Project/Task/Feature/Session/...
│   ├── config.py         #   DEVDASH_HOME + config.json (separate from data)
│   ├── events.py         #   append-only event bus (real-time)
│   ├── projects.py / tasks.py / sessions.py / logs.py / features.py
│   ├── gitwatch.py       #   read-only Git + commit message parsing
│   ├── stats.py / status.py / demo.py
│   └── ai/               #   abstract provider + ollama / local / cli / anthropic /
│                         #   mistral / openai / openai_compat / mock + compact context
│                         #   + assistant (context/ask/summarize/plan/verdict)
├── cli/                  # typer + rich; only formats and forwards to the core
│   ├── main.py           #   entry point, global --json option
│   └── commands/         #   projects/task/session/log/git/feature/ai/stats/demo/gui/status
├── gui/                  # native PyWebView window + stdlib 127.0.0.1 HTTP server
│   ├── server.py         #   API GET/POST routes + background Git watcher
│   ├── api.py            #   all endpoints (same core functions as the CLI)
│   ├── desktop.py        #   run(): native window, or --browser
│   └── static/           #   index.html / app.js / styles.css (dark theme)
│                         #   + vendor/chart.umd.min.js (vendored Chart.js, offline)
└── tests/                # pytest (228+ tests)
```

**Why Python**: rich stdlib (sqlite3, http.server, json, dataclasses), trivial
deployment via `pip install -e .` on all three OSes, PyWebView gives a real native
window without embedding Chromium (~200 MB saved vs Electron), and typing
(dataclasses + `from __future__ import annotations`) stays clean.
Accepted trade-off: two CLI dependencies (typer/rich) and one GUI dependency
(pywebview) — the core stays 100 % stdlib.

**Why a native window instead of a web app**: no browser tab to babysit, direct
startup, full control of the lifecycle, and the page is still served locally so it
works offline. RAM: one Python process + the system WebView2 (~30–60 MB), far less
than an Electron app.

## 8. Tests

```bash
pip install -e ".[test]"
PYTHONPATH=. python -m pytest -q   # 228+ tests: core, CLI, sessions, logs, git, GUI,
                                   # features, AI, stats, status, review, security
```

## 9. Deliberate limitations

No cloud, no accounts, no multi-device sync, no team features, no Git writes, no
email notifications. AI is an optional layer: without it, nothing is missing.

**No Docker, on purpose**: DevDash is local by design — CLI and native window on
the developer's machine, SQLite data in `~/.devdash`, git repos read locally, GUI
server bound to `127.0.0.1` only. A container would have to mount all those
volumes (home, repos): no benefit, only complexity.
Cross-OS portability is already covered by Python 3.11+ (`pip` install) and by the
standalone executable (`scripts/build_exe.py`). Even to run the GUI in `--browser`
mode on a NAS, the executable or the venv runs there directly, no container needed.
