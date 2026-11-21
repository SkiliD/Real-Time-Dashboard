# DevDash

DevDash is a small **personal dashboard that lives on your computer** to help you
track your projects — game dev, web, anything. It shows your tasks, how much time
you spend working, your notes, and what your Git commits say, all in one place.

There is a **command line** for quick actions and a **desktop app** for everything
else. Both share the same data, so anything you do in one appears instantly in the
other.

Everything stays on your machine: **no account, no cloud, no internet needed**.

> 🇫🇷 Version française : [README.fr.md](README.fr.md)

---

## What can it do?

- **Projects**: several at once (Unity, Unreal, Godot, web…), one "active" at a time.
  Switching never loses or mixes anything.
- **Tasks**: a to-do list with priorities, tags (`gameplay`, `ui`…), time estimates
  and sub-tasks, shown as a kanban board.
- **Work timer**: start/stop sessions, with a Pomodoro mode (25 min focus + 5 min
  break by default). Each session can end with a note: what you did, what's blocking.
- **Devlog**: quick dated notes — decisions, ideas, blockers. Searchable. This is
  your long-term memory.
- **Git, read-only**: link a repository and DevDash shows your commits (branch, last
  commit, commits per day) and links them to tasks automatically (`#42`,
  `feat(dash): …`). It **only reads** — it never changes your repo.
- **Stats**: time worked per day, tasks done, day streak, charts.
- **Features**: group tasks into a feature. A feature is only marked "done" **by
  you** — never automatically.
- **Optional AI assistant**: ask questions about your project ("where do I stand?"),
  get a weekly summary, split a big task into sub-tasks. Works with local models
  (Ollama, LM Studio, any local AI) or cloud APIs (Claude, Mistral, OpenAI).
  Fully optional — without it, nothing is missing.

## Install

You need **Python 3.11 or newer**.

**Windows**
```powershell
py -3 -m venv .venv
.venv\Scripts\activate
pip install -e ".[gui,test]"
```

**macOS / Linux**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[gui,test]"
```

Or without a virtualenv: `pipx install .`

Handy alias (optional): `alias dd='devdash'` in your `~/.bashrc` / `~/.zshrc`
(`dd.bat` calling `devdash %*` on Windows).

### No Python? Build a standalone app

```bash
pip install -e ".[build]"
py -3 scripts/build_exe.py        # -> dist/devdash.exe (Windows) / dist/devdash (macOS/Linux)
```

Build it on the OS where you'll use it. The result is one double-clickable file —
put it on a USB stick if you want. (`--onedir` for a folder build, `--no-gui` for a
smaller CLI-only build.)

## First steps

```bash
devdash demo seed        # fill with demo data to explore safely
devdash gui              # open the desktop app
devdash status           # text snapshot of the active project
```

With a real project:

```bash
devdash projects create "Aether Vale" --engine unity --set-active
devdash git link "C:\path\to\my\repo"
devdash task add "Player dash" --priority high --tag gameplay --est 90
devdash session start --task 1
devdash session stop --note "dash works, needs polish"
devdash log add "decision: 0.15s input buffer on the dash"
devdash stats week
```

Add `--json` before any command for machine-readable output (for scripts).

## The desktop app

`devdash gui` opens a **native window** (not a browser tab): dark theme, works
offline. Tabs: **Tasks** (kanban), **Devlog**, **Git**, **Features**, **Stats**
(charts), **AI Assistant** (only if enabled). Everything updates live — including
new Git commits, which a background watcher picks up every 20 seconds.

`devdash gui --browser` does the same thing in your browser, at
`http://127.0.0.1:8765`.

## Command cheat sheet

| You want to… | Command |
|---|---|
| see projects / switch active | `devdash projects list` · `devdash projects set-active aether-vale` |
| add / list / finish a task | `devdash task add "Dash" --tag gameplay --est 90` · `devdash task list` · `devdash task done 3` |
| filter tasks | `devdash task list --status todo --tag ui --feature dash` |
| start / stop the timer | `devdash session start --task 3 --pomodoro` · `devdash session stop --note "…"` |
| write in the devlog | `devdash log add "idea: chained air dash"` · `devdash log search "dash"` |
| see Git activity | `devdash git status` · `devdash git log --task 3` · `devdash git sync` |
| validate a feature (only you can) | `devdash feature add dash` · `devdash feature link dash 3 4` · `devdash feature done dash` |
| see your stats | `devdash status` · `devdash stats week` · `devdash stats streak` |
| back up everything | `devdash backup --out D:\backups` (timestamped zip) |
| export / re-import data | `devdash export --out export.json` · `devdash import export.json` |
| AI help | `devdash ai ask "where do I stand?"` · `devdash ai plan "combat system" --apply` · `devdash ai context` |
| connect an AI agent (Claude Code…) | `devdash mcp` — see below |

## The AI assistant (optional)

Off by default. Enable it in `~/.devdash/config.json`:

```json
{ "ai_enabled": true, "ai_provider": "ollama", "ai_model": "qwen3:8b" }
```

Pick a provider:

- **`ollama`** — a local model via [Ollama](https://ollama.com). Nothing leaves your PC.
- **`local`** — any local server that speaks the OpenAI API (LM Studio, llama.cpp…).
- **`cli`** — any local AI you run as a command (`ai_cli_command: "llama-cli -m model.gguf"`).
- **`anthropic` / `mistral` / `openai` / `openai_compat`** — cloud APIs; you provide
  `ai_api_key`. DevDash then shows a visible warning, because your project summary
  is sent to that service.

Two safety habits built in: the AI only ever receives a **short summary** of your
project (never the raw database) — check it anytime with `devdash ai context` — and
it **never writes anything without you saying so** (`--apply` in the CLI, a confirm
button in the app). API keys live only in `config.json` (a file readable by you
alone), never in the data, logs, or error messages.

## Where your data lives

Everything is in **one folder**: `~/.devdash/`

```
~/.devdash/
├── devdash.db     # all your data (a single SQLite file)
└── config.json    # settings + API keys (only you can read it)
```

- **Backup** = copy that folder (or run `devdash backup`).
- Move it anywhere with the `DEVDASH_HOME` environment variable (USB stick, synced
  folder, another computer).
- The config file is kept **separate** from the data on purpose: backing up your
  data never includes your API keys.

## Privacy & security, in plain words

- The desktop app only listens on `127.0.0.1` (your machine, not the network), and
  checks that requests really come from it — so a random website can't mess with it.
- Git is read-only: DevDash runs `git log`, never `git commit`/`push`/etc.
- The database and config files are set readable by your user only.
- Your API keys never appear in logs, errors, or the database.

## Connect DevDash to an AI agent (MCP)

`devdash mcp` starts a small local MCP server so agents like **Claude Code** can
ask DevDash about your project (status, tasks, stats…) and add tasks/logs/sessions.

- Claude Code: add `.mcp.json` at your project root:
  ```json
  { "mcpServers": { "devdash": { "command": "devdash", "args": ["mcp"] } } }
  ```
- Vibe: `~/.vibe/config.toml` → `[mcp_servers.devdash]` with `command = "devdash"`, `args = ["mcp"]`.

Same rules as everywhere: no destructive actions, Git stays read-only, and a
feature is never marked done by an agent — only by you.

To auto-refresh Git commits after agent-made commits, add a post-tool hook that runs
`devdash git sync` (see `README.fr.md` for ready-to-paste Vibe/Claude Code snippets).

## Language

The whole app (CLI + GUI) is translated. Set `"lang": "fr"` or `"en"` in
`config.json` — it applies immediately. Commands and stored values stay in English
so scripts keep working.

## For developers

Architecture in one breath: a **stdlib-only Python core** (SQLite + event bus) does
all the work; the CLI (typer/rich) and the GUI (local HTTP server + native PyWebView
window) are thin shells over the same core, which is why they always agree.

```bash
PYTHONPATH=. python -m pytest -q    # 119 tests
```

Optional extras: `pip install playwright && playwright install chromium` enables a
real-browser GUI test (it skips automatically otherwise).

## What DevDash deliberately is not

No cloud, no accounts, no team features, no multi-device sync, no email — and no
Docker either: it's a local tool, so a container would only add complexity. The AI
is a bonus layer, never a requirement.
