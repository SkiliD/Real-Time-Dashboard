# DevDash

DevDash is a small **personal dashboard that lives on your computer** to help you
track your projects — game dev, web, anything. It shows your tasks, how much time
you spend working, your notes, and what your Git commits say, all in one place.
I built it for my own game projects — if it fits yours too, great.

There is a **command line** for quick actions and a **desktop app** for everything
else. Both share the same data, so anything you do in one appears instantly in the
other.

Everything stays on your machine: **no account, no cloud, no internet needed**.

> 🇫🇷 Version française : [README.fr.md](README.fr.md)

---

## What can it do?

- **Projects**: several at once (Unity, Unreal, Godot, web…), one "active" at a time.
  Switching never loses or mixes anything. **Connect an existing project**: the
  "folder" field in the "New project" window (or `devdash projects connect <path>`)
  — engine auto-detected (Unity/Unreal/Godot/web via marker files), enclosing git
  repo auto-linked read-only. From the sidebar (✎ button on hover): rename, change
  engine/color/status, or permanently delete (with confirmation).
- **Tasks**: a to-do list with priorities, tags (`gameplay`, `ui`…), time estimates,
  subtasks and search, shown as a **drag & drop kanban** — click a card to edit it
  (title, description, priority, tags, parent, feature).
- **Work timer**: start/stop sessions (focus / break / custom, custom duration,
  Pomodoro mode 25+5 min by default). Each session can end with a note: what you
  did, what's blocking.
- **Devlog**: quick dated notes — decisions, ideas, blockers. Searchable, editable,
  deletable. This is your long-term memory.
- **Git, read-only**: link a repository and DevDash shows your commits (branch, last
  commit, commits per day) and links them to tasks automatically (`#42`,
  `feat(dash): …`). It **only reads** — it never changes your repo. One explicit
  exception: `devdash git ignore` (or the "Add to .gitignore" button in the Git tab)
  writes a **marker-delimited block** into YOUR repo's `.gitignore` so DevDash local
  files (db, config, backups, exports) never get committed — idempotent, never
  duplicated.
- **Stats**: work time, tasks done, day streak, progress — with **filters**: by project
  (active / one / all projects), period (7 to 365 days), grouping (day / week / month),
  plus a "time by project" comparison chart.
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

The native window (PyWebView) needs one system package on **Linux** — WebKit2GTK:
```bash
# Debian/Ubuntu:  sudo apt install python3-gi gir1.2-webkit2-4.1
# Fedora:         sudo dnf install webkit2gtk4.1 python3-gobject
# Arch:           sudo pacman -S webkit2gtk python-gobject
```
On **macOS** nothing extra is needed. On a headless server (no display), just use
`devdash gui --browser` — it opens in your browser, same app.

Or without a virtualenv: `pipx install .`

> **Windows — AppLocker/WDAC note**: if `devdash` fails with *"an application control
> policy blocked this file"* (the pip-generated `devdash.exe` launcher is unsigned),
> run DevDash through the interpreter instead: `.venv\Scripts\python.exe -m devdash gui`
> (or `py -m devdash gui`). A `devdash.bat` shim calling `python -m devdash %*` works too —
> `.bat` files are not covered by the executable block.

Handy alias (optional): `alias dd='devdash'` in your `~/.bashrc` / `~/.zshrc`
(`dd.bat` calling `devdash %*` on Windows).

### No Python? Build a standalone app

```bash
pip install -e ".[build]"
py -3 scripts/build_exe.py        # -> dist/devdash/devdash.exe (Windows) / dist/devdash/ (macOS/Linux)
```

Build it on the OS where you'll use it. The result is a folder with one
double-clickable exe inside — put the folder on a USB stick if you want.
Flags: `--onefile` for a single file, `--no-gui` for a smaller CLI-only build,
`--no-console` for a windowed exe (no console flash).

> **Windows SmartScreen / antivirus**: the exe is *unsigned*, so the first launch may
> show "Windows protected your PC" — click **More info → Run anyway**. The build is
> AV-friendly by construction (onedir folder instead of a self-extracting onefile, UPX
> off, Windows version metadata + `asInvoker` manifest embedded), but only a *trusted
> code-signing certificate* removes the warning for good:
> `.\scripts\sign_exe.ps1 -ExePath "dist\devdash\devdash.exe"`.
> DevDash is local-only: no account, no telemetry, no network calls except an explicit
> `git fetch`/AI provider call you trigger yourself. To whitelist in Defender:
> *Windows Security → Virus & threat protection → Exclusions → add the `dist\devdash` folder*.

## First steps

```bash
devdash demo seed        # fill with demo data to explore safely
devdash gui              # open the desktop app
devdash status           # text snapshot of the active project
```

With a real project:

```bash
devdash projects connect "C:\path\to\my\project"   # EXISTING folder: engine auto-detected (Unity/Unreal/Godot/web), git repo auto-linked read-only
# or by hand:
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
| see projects / switch active | `devdash projects list` · `devdash projects set-active aether-vale` · `devdash projects show aether-vale` · `devdash projects update aether-vale --status beta` · `devdash projects archive aether-vale` · `devdash projects delete aether-vale` |
| connect an existing folder | `devdash projects connect "C:\games\aether-vale"` (engine + git auto-detected) |
| add / list / finish a task | `devdash task add "Dash" --tag gameplay --est 90` · `devdash task list` · `devdash task done 3` · `devdash task start 3` · `devdash task cancel 3` · `devdash task reopen 3` · `devdash task edit 3 --priority high` · `devdash task show 3` · `devdash task delete 3` |
| filter tasks | `devdash task list --status todo --tag ui --feature dash` |
| start / stop the timer | `devdash session start --task 3 --pomodoro` · `devdash session stop --note "…"` · `devdash session now` · `devdash session list --week` |
| write in the devlog | `devdash log add "idea: chained air dash"` · `devdash log search "dash"` · `devdash log recent` · `devdash log edit 2 --tags idea` · `devdash log delete 2` |
| see Git activity | `devdash git status` · `devdash git log --task 3` · `devdash git sync` · `devdash git link <path>` · `devdash git unlink` · `devdash git ignore` |
| manage features (only you validate) | `devdash feature add dash` · `devdash feature list` · `devdash feature show dash` · `devdash feature link dash 3 4` · `devdash feature unlink dash 3` · `devdash feature done dash` · `devdash feature approve dash` · `devdash feature reject dash` · `devdash feature edit dash --name air-dash` · `devdash feature delete dash` · `devdash feature reopen dash` |
| see your stats | `devdash status` · `devdash stats today` · `devdash stats week` · `devdash stats month` · `devdash stats streak` · `devdash stats tags` · `devdash stats all` |
| back up everything | `devdash backup --out D:\backups` (timestamped zip) |
| export / re-import data | `devdash export --out export.json` · `devdash import export.json` |
| AI help | `devdash ai ask "where do I stand?"` · `devdash ai plan "combat system" --apply` · `devdash ai verify 3` · `devdash ai context` |
| connect an AI agent (Claude Code…) | `devdash mcp` — see below |

## Updating after a `git pull`

```bash
devdash update           # ff-only pull + dependency reinstall (git installs)
devdash update --check   # only check whether an update exists
devdash update status    # install mode + git info (no network)
devdash version          # version + git hash of the checkout
```
From the GUI: **⚙ Settings → Update** (check / update in one click, then restart the
GUI). pip/pipx installs: `pip install --upgrade devdash` / `pipx upgrade devdash`.
Standalone executable: replace it or rebuild it (`py -3 scripts/build_exe.py`).
Never force-pulls, and refuses to update while the repo has uncommitted changes.

## Settings (⚙ tab)

Everything is adjustable from the app itself: AI provider (local or cloud) with
a connection test, **interface language** (English/French, applies instantly),
**Pomodoro** durations, **Git watcher** interval, **GUI port**, **commit
patterns** linking commits to tasks, and a **"Back up now"** button (timestamped
zip of the data folder). No file editing needed.

## The AI assistant (optional)

Off by default. Easiest way: open the GUI's **⚙ Settings** tab, pick a provider
(local or cloud), fill in the model/key, hit *Test connection* then *Save* — no
file editing needed. Or do it by hand in `~/.devdash/config.json`:

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
PYTHONPATH=. python -m pytest -q    # 228+ tests
```

Optional extras: `pip install playwright && playwright install chromium` enables a
real-browser GUI test (it skips automatically otherwise).

📚 **Going further** (full technical reference: every command, SQL schema, Vibe /
Claude Code hooks, MCP, architecture, security details): [docs/README.detail.en.md](docs/README.detail.en.md)

## What DevDash deliberately is not

No cloud, no accounts, no team features, no multi-device sync, no email — and no
Docker either: it's a local tool, so a container would only add complexity. The AI
is a bonus layer, never a requirement.
