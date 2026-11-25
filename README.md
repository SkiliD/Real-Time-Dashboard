# DevDash

DevDash is a small **personal dashboard that lives on your computer** to track your
projects — game dev, web, anything: tasks, work time, notes, and what your Git
commits say, all in one place. A **command line** for quick actions and a **desktop
app** for the rest, both sharing the same data. Everything stays on your machine:
**no account, no cloud, no internet needed**.

> 🇫🇷 Version française : [README.fr.md](README.fr.md)

## What it does

- **Projects** — several at once (Unity, Unreal, Godot, web…); connect an existing
  folder (`devdash projects connect <path>`): engine auto-detected, enclosing git
  repo auto-linked read-only.
- **Tasks** — priorities, tags, estimates, subtasks, drag & drop **kanban**.
- **Work timer** — focus/break sessions, optional Pomodoro, end-of-session notes.
- **Devlog** — dated notes: decisions, ideas, blockers.
- **Git, read-only** — commits auto-linked to tasks (`#42`, `feat: …`); the only
  remote commands are read-style and only when you ask: `git fetch`,
  `git pull --ff-only` (fast-forward only, never forced), `git remote -v`. GUI Git
  tab: Fetch / Pull buttons, per-file checkboxes for a combined diff, live refresh.
  A push is refused by design.
- **Stats** — time, tasks done, streak, progress; filters by project / period /
  grouping, with charts.
- **Features** — group tasks into a feature; marked "done" **only by you**.
- **Optional AI assistant** — local models (Ollama, LM Studio…) or cloud APIs;
  never writes anything without your explicit OK.

## Install

Requires **Python 3.11+**.

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
# Linux only: the native window also needs WebKit2GTK
# Debian/Ubuntu: sudo apt install python3-gi gir1.2-webkit2-4.1
# Fedora:        sudo dnf install webkit2gtk4.1 python3-gobject
# Arch:          sudo pacman -S webkit2gtk python-gobject
```

Headless (no display)? `devdash gui --browser` — same app in your browser.
Without a virtualenv: `pipx install .`

> **Windows — AppLocker/WDAC**: if the pip-generated `devdash.exe` launcher is blocked
> by an application-control policy, run through the interpreter instead:
> `.venv\Scripts\python.exe -m devdash gui` (a `devdash.bat` shim works too).

### No Python? Build a standalone executable

```bash
pip install -e ".[build]"
devdash build        # -> dist/devdash/devdash.exe  (also: ⚙ Settings → "Executable (.exe)" card)
```
Flags: `--onefile` (single file), `--no-gui` (CLI-only), `--no-console` (windowed exe).
Build it on the OS where you'll use it — the result is a double-clickable folder,
USB-stick ready.

> **Windows SmartScreen / antivirus**: the exe is *unsigned*, so the first launch may
> show "Windows protected your PC" → **More info → Run anyway**. Only a trusted
> code-signing certificate removes the warning for good:
> `.\scripts\sign_exe.ps1 -ExePath "dist\devdash\devdash.exe"`.
> DevDash is local-only: no account, no telemetry, no network calls except an explicit
> `git fetch` or AI-provider call you trigger yourself.

## Quick start

```bash
devdash demo seed                                  # demo data to explore safely
devdash gui                                        # desktop app
devdash projects connect "C:\path\to\my\project"   # existing folder: engine + git auto-detected
devdash task add "Player dash" --tag gameplay --est 90
devdash session start --task 1
devdash session stop --note "works, needs polish"
devdash log add "decision: 0.15s input buffer on the dash"
devdash stats week
```
Add `--json` before any command for machine-readable output.

## Desktop app

`devdash gui` opens a **native frameless window** (not a browser tab): its own title
bar (drag to move, minimize/maximize/close buttons, double-click to maximize), the
app icon in the **taskbar / Alt-Tab**, and a layout that adapts to any window size.
Tabs: **Tasks** (kanban), **Devlog**, **Git**, **Features**, **Stats**, **AI
Assistant** — everything live, including Git commits (background watcher).
`devdash gui --browser` runs the same app in your browser at `http://127.0.0.1:8765`.

## Main commands

| You want to… | Command |
|---|---|
| projects | `devdash projects connect "<path>"` · `projects list` · `projects set-active <slug>` |
| tasks | `devdash task add "…" --tag gameplay --est 90` · `task list` · `task done 3` · `task start 3` |
| timer | `devdash session start --task 3 [--pomodoro]` · `devdash session stop --note "…"` |
| devlog | `devdash log add "…"` · `devdash log search "…"` |
| git | `devdash git status` · `git log` · `git fetch` · `git pull` (ff-only) · `git sync` |
| stats / snapshot | `devdash status` · `devdash stats week` · `devdash stats all` |
| backup / export | `devdash backup` · `devdash export --out export.json` · `devdash import export.json` |
| update | `devdash update` (ff-only pull + deps) · `devdash update --check` |
| build the exe | `devdash build` |
| connect an AI agent (Claude Code…) | `devdash mcp` (config snippet: `devdash mcp-config vscode`) |

📚 Full technical reference — every command, SQL schema, Vibe/Claude Code hooks,
MCP tools, architecture, security details: [docs/README.detail.en.md](docs/README.detail.en.md)
Illustrated GUI guide (first-launch tour, kanban, sessions, git): [docs/guide-utilisation.en.md](docs/guide-utilisation.en.md)

## Settings (⚙ tab)

Everything is adjustable from the app itself: AI provider + connection test,
**language** (EN/FR, instant), **theme** (auto/dark/light), Pomodoro durations, git
watcher interval, GUI port, commit patterns, automatic backups, one-click backup,
build-exe and update cards. No file editing needed.

## Where your data lives

One folder: `~/.devdash/`

```
~/.devdash/
├── devdash.db     # all your data (a single SQLite file)
└── config.json    # settings + API keys (readable by you alone)
```

Backup = copy that folder (or `devdash backup`). Move it anywhere with the
`DEVDASH_HOME` environment variable. Config is kept separate from data on purpose:
backing up your data never includes your API keys.

## Privacy & security, in plain words

- The desktop app listens on `127.0.0.1` only and checks Host/Origin — a random website
  can't drive it.
- Git stays read-only: DevDash runs `git log`, never `git commit`/`push`/etc. The only
  repo write ever made is the marker-delimited `.gitignore` block, on your explicit
  request (`devdash git ignore`).
- DB and config files are readable by your user only; API keys never appear in logs,
  errors, or the database.
- The AI receives a short project summary, never the raw database — check it anytime
  with `devdash ai context`.

## Language

The whole app (CLI + GUI) is translated. Set `"lang": "fr"` or `"en"` in
`config.json` — applies immediately. Commands and stored values stay in English so
scripts keep working.

## For developers

A **stdlib-only Python core** (SQLite + event bus) does all the work; the CLI
(typer/rich) and the GUI (local HTTP server + native PyWebView window) are thin shells
over the same core — that's why they always agree.

```bash
PYTHONPATH=. python -m pytest -q    # 460+ tests
```

## What DevDash deliberately is not

No cloud, no accounts, no team features, no multi-device sync, no email — and no
Docker either: it's a local tool, a container would only add complexity. The AI is a
bonus layer, never a requirement.
