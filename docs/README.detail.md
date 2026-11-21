# DevDash — référence technique détaillée

> Version longue et technique. Pour la version courte et lisible : [README.md](../README.md) (anglais) / [README.fr.md](../README.fr.md) (français).
>
> 🇬🇧 English version of this detailed reference: [README.detail.en.md](README.detail.en.md). The main (short) README is [README.md](../README.md).


> Dashboard de suivi de dev **100 % local et portable**, pour développeur solo
> (jeux vidéo ou tout autre projet). CLI + application graphique native sur un
> **cœur partagé** : une action faite au terminal apparaît instantanément dans
> la GUI, et inversement. Stockage SQLite dans un seul dossier copiable.
> Intégration Git **en lecture seule**. Assistant IA **optionnel**.

- Sans cloud, sans compte, sans serveur distant.
- Agnostique du moteur : Unity, Unreal, Godot, web, ou autre.
- Multi-projets : changer de projet actif ne touche jamais aux données des autres.
- La GUI est une **vraie fenêtre native** (PyWebView / WebView2 sous Windows), pas une web app : pas d'onglet navigateur, RAM maîtrisée, aucun lien avec Internet requis (Chart.js est embarqué localement).

---

## 1. Installation

Prérequis : **Python 3.11+** (testé avec 3.13/3.15). Aucune dépendance pour le cœur.

### Windows

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
pip install -e ".[gui,test]"     # CLI + fenêtre native (pywebview) + tests
```

### macOS / Linux

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[gui,test]"
```

Variante sans venv (installation utilisateur) :

```bash
pipx install .          # ou: pip install --user -e .
```

Sans installation (depuis le dépôt) : `python -m devdash --help` fonctionne
directement (avec `PYTHONPATH` sur le dossier du projet).

### Alias `dd` (optionnel mais recommandé)

- **Windows** — créer `dd.bat` dans un dossier du `PATH` :
  ```bat
  @echo off
  py -3 -m devdash %*
  ```
  (ou `devdash %*` si installé via pip/pipx)
- **macOS / Linux** — dans `~/.bashrc` ou `~/.zshrc` :
  ```bash
  alias dd='devdash'
  ```

### Exécutable autonome (sans installer Python)

PyInstaller fabrique un binaire unique, double-cliquable :

```bash
pip install -e ".[build]"            # ajoute pyinstaller
py -3 scripts/build_exe.py           # -> dist/devdash.exe (Windows) / dist/devdash (macOS, Linux)
py -3 scripts/build_exe.py --onedir  # build dossier (démarrage plus rapide, plus simple à whitelister côté antivirus)
py -3 scripts/build_exe.py --no-gui  # exe CLI seul (plus petit, sans pywebview)
```

- **Buildez sur l'OS cible** (un exe PyInstaller n'est pas cross-platform).
- L'exécutable stocke toujours ses données dans `DEVDASH_HOME` (`~/.devdash`) :
  le binaire est **portable** (copiez-le sur une clé USB), la mise à jour = remplacer
  le fichier, la migration = copier `~/.devdash`.
- Mode GUI (`devdash gui`) : sous Windows, WebView2 est requis (préinstallé sur
  Windows 11 ; sinon `devdash gui --browser` fonctionne partout).
- L'exécutable utilise le même point d'entrée que `python -m devdash` ; toutes les
  commandes du README fonctionnent à l'identique (`devdash.exe status`, `--json`…).
- Faux positifs antivirus possibles avec le mode onefile (UPX est désactivé dans
  `devdash.spec`) : préférez `--onedir` ou signez le binaire en entreprise.

## 2. Démarrage rapide

```bash
devdash demo seed                 # données de démo pour explorer sans vrai projet
devdash gui                       # lance la fenêtre graphique native
devdash status                    # instantané du projet actif
devdash projects list
```

Ou avec un vrai projet :

```bash
devdash projects create "Aether Vale" --engine unity --status development --set-active
devdash git link "C:\chemin\vers\mon\repo"     # liaison lecture seule au dépôt Git
devdash task add "Dash du joueur" --priority high --tag gameplay --est 90
devdash session start --task 1                 # minuteur interactif
devdash session stop --note "dash OK, à peaufiner"
devdash log add "décision : input buffer de 0.15s sur le dash"
devdash status
```

## 3. Lancer la GUI

```bash
devdash gui                 # fenêtre native (PyWebView, WebView2 sur Windows)
devdash gui --browser       # repli : ouvre http://127.0.0.1:8765 dans le navigateur
devdash gui --port 9000     # port personnalisé (défaut : config gui_port = 8765)
```

La GUI sert une page locale (thème sombre) depuis un petit serveur HTTP
`127.0.0.1` interne et fonctionne **hors-ligne**. Les mises à jour sont
temps réel : la GUI interroge le bus d'événements SQLite toutes les 1,5 s
et un watcher Git scanne les dépôts liés toutes les `git_watch_interval_sec`
(20 s par défaut) — un nouveau commit rafraîchit le dashboard sans redémarrage.

Onglets : **Tâches** (kanban), **Devlog**, **Git**, **Features**, **Stats**
(graphiques Chart.js embarqués), **Assistant IA** (visible uniquement si l'IA
est activée), **Réglages** (toujours visible : provider IA local ou cloud +
test de connexion + fonctions actives, mais aussi langue de l'interface,
Pomodoro, intervalle du watcher Git, port de la GUI, patterns de commits,
et bouton de sauvegarde zip).

## 4. Référence CLI

Sortie lisible par humain par défaut ; ajoutez `--json` (option globale, avant
la commande) pour une sortie machine.

| Commande | Exemple | Description |
|---|---|---|
| `projects list` | `devdash projects list` | liste, `*` = actif |
| `projects create` | `devdash projects create "Aether Vale" --engine unity --status development --color #7c5cff --set-active` | `--engine`, `--status`, `--color`, `--description`, `--set-active` |
| `projects connect <path>` | `devdash projects connect "C:\jeux\aether-vale" --set-active` | **connecte un dossier existant** : moteur auto-détecté (`ProjectSettings/ProjectVersion.txt` → unity, `*.uproject` → unreal, `project.godot` → godot, `package.json` → web), nom suggéré depuis le dossier, racine git trouvée en remontant l'arborescence et **auto-liée en lecture seule** (commits importés immédiatement) ; `--no-git` pour refuser, `--engine` pour forcer |
| `projects set-active <ref>` | `devdash projects set-active aether-vale` | **sans perte** : simple pointeur, les autres projets gardent tout |
| `projects show/update/archive` | `devdash projects show aether-vale` | détail, édition, archivage. **Suppression dure** : possible depuis la GUI (sidebar ✎ → Supprimer, confirmation en 2 clics) via `POST /api/projects/delete` — cascade complète (tâches, sessions, devlog, commits liés), le projet actif est réassigné |
| `task add` | `devdash task add "Dash" -p high -t gameplay -t combat --est 90` | `-p` projet (défaut: actif), `--parent` sous-tâche |
| `task list` | `devdash task list -s todo --priority high --tag ui --feature dash` | filtres statut/priorité/tag/feature |
| `task start/done/cancel/reopen <id>` | `devdash task done 3` | changement de statut |
| `task edit <id>` | `devdash task edit 3 --priority critical --tag vfx` | édition |
| `session start` | `devdash session start --task 3 --kind focus --pomodoro` | minuteur interactif ; `--background` pour rendre la main, `--pomodoro` = après le focus, propose de lancer la pause automatiquement |
| `session stop` | `devdash session stop --note "fait: X, bloqué: Y"` | durée + note de fin |
| `session now` / `session list` | `devdash session list --week` | session en cours / historique (`--today`, `--week`, `-t <id tâche>`) |
| `log add` | `devdash log add "idée: dash aérien chaîné" --tag idea` | entrée de devlog |
| `log recent` / `log search` | `devdash log search "cooldown"` | journal daté, recherchable |
| `git link <path>` | `devdash git link "C:\repo"` | lie le projet actif à un dépôt (lecture seule) |
| `git sync` | `devdash git sync` | importe les nouveaux commits (tous projets si `-p` omis) |
| `git log` | `devdash git log --task 3` | commits liés à une tâche (`--unparsed` = non reconnus) |
| `git status` | `devdash git status` | branche, dernier commit, commits/jour |
| `git ignore` | `devdash git ignore [--repo <path>]` | écrit/rafraîchit le **bloc balisé** `# >>> devdash >>>` dans le `.gitignore` du dépôt lié : `.devdash/`, `devdash.db*`, snapshots, `devdash-backup-*.zip`, `devdash-export-*.json`, et `<home>/config.json` si `DEVDASH_HOME` est dans le dépôt (clés API). Idempotent (jamais dupliqué), contenu existant préservé. **Seul fichier que DevDash écrit dans votre dépôt, et seulement sur cette commande explicite** — l'historique git reste intouché |
| `feature add/link/done` | `devdash feature add dash` ; `devdash feature link dash 3 4` ; `devdash feature done dash` | **validation manuelle** d'une feature |
| `feature approve/reject` | `devdash feature reject dash --feedback "pas assez testé"` | approuver/rejeter une proposition (le rejet est mémorisé) |
| `status` | `devdash status` ; `devdash status --watch` | instantané temps réel (tâches, session, streak, git) |
| `stats today/week/month/streak/tags/all` | `devdash stats week` | temps, tâches faites, streak, répartition par tag |
| `ai context` | `devdash ai context` | **affiche le contexte exact** qui serait envoyé à l'IA — rien n'est envoyé, même IA activée |
| `ai ask` | `devdash ai ask "où en suis-je ?"` | chat (si IA activée) |
| `ai summarize` | `devdash ai summarize --period week` | résumé de la semaine |
| `ai plan` | `devdash ai plan "système de combat" --apply` | découpe en sous-tâches (`--apply` = seule écriture possible) |
| `ai verdict` | `devdash ai verdict dash` | proposition de validation IA (jamais automatique) |
| `demo seed` | `devdash demo seed` | données de démo (`--force` si des projets existent) |
| `backup` | `devdash backup --out D:ackups` | **zip horodaté** de `~/.devdash` (db + config), snapshot SQLite cohérent même GUI ouverte |
| `export` | `devdash export --out export.json` | **export JSON complet** (toutes les tables) pour archive/migration/inspection — lecture seule |
| `import` | `devdash import export.json [--force]` | restaure un export JSON dans la base (refusé si la base n’est pas vide, sauf `--force`) |
| `mcp` | `devdash mcp` | serveur **MCP** (JSON-RPC stdio) pour connecter DevDash à un agent IA externe |
| `gui` | `devdash gui` | fenêtre graphique native |
| `version` | `devdash version` | version + hash git du checkout |
| `update` | `devdash update [--check] [--no-reinstall]` · `update check` · `update status` | **auto-mise à jour** : install git → `git pull --ff-only` (jamais de force, refus si arbre sale) + `pip install -e .` pour rafraîchir les dépendances ; install pip/pipx → affiche la bonne commande ; exécutable → rebuild. `restart_needed` retourné. GUI : carte « Mise à jour » dans Réglages (`GET /api/update/status`, `POST /api/update/check`, `POST /api/update`) |

### Conventions de commits Git

Les messages sont reliés aux tâches/features par des regex configurables
(`commit_patterns` dans `config.json`) :

- `#42` → tâche n°42
- `task-42: ...` → tâche n°42
- `feat(dash): ...` → feature « dash »

Les commits non reconnus restent listés à part (`git log --unparsed`).
**Aucune opération d'écriture Git** : DevDash ne fait que `git log` / `git rev-parse`.

### Synchronisation auto des commits (optionnel, avec Vibe)

Pour que `devdash git sync` se lance automatiquement après vos commits :

**Avec Vibe** — hook dans `~/.vibe/hooks.toml` :

```toml
[[hooks]]
name = "devdash-git-sync"
on = "post_tool"
tools = ["re:bash", "re:process_start"]
command = "devdash git sync"
```

**Avec Claude Code** — hook `PostToolUse` dans `~/.claude/settings.json` :

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

Ainsi, un commit fait depuis n'importe quel agent (Vibe, Claude Code, ou vous)
apparaît dans la GUI DevDash sans redémarrage.

### Connecter DevDash à un agent IA via MCP (Model Context Protocol)

`devdash mcp` lance un **serveur MCP stdlib** (JSON-RPC sur stdio, aucun paquet
supplémentaire) qui expose DevDash aux agents externes — lecture + actions sûres
(ajout de tâche/log, session, stats, git en lecture seule…) :

**Claude Code** — `.mcp.json` à la racine du projet :
```json
{ "mcpServers": { "devdash": { "command": "devdash", "args": ["mcp"] } } }
```
(avec l’exécutable autonome : `"command": "D:\tools\devdash.exe"`)

**Vibe** — `~/.vibe/config.toml` (ou `.vibe/` du projet) :
```toml
[mcp_servers.devdash]
command = "devdash"
args = ["mcp"]
```

Outils exposés : `devdash_status`, `devdash_projects_list`, `devdash_tasks_list`,
`devdash_task_add`, `devdash_task_set_status`, `devdash_session_start/stop`,
`devdash_log_add/search`, `devdash_stats`, `devdash_git_status/log`,
`devdash_features_list`, `devdash_ai_ask` (si l’IA est activée).
Règle identique à partout : **aucune action destructive**, git en lecture seule,
et une feature n’est **jamais** marquée terminée par l’agent.

## 5. Données, portabilité, sauvegarde

Toutes les données tiennent dans **un seul dossier** :

```
~/.devdash/                  # DEVDASH_HOME (modifiable par variable d'env)
├── devdash.db               #   toutes les données (SQLite, WAL)
└── config.json              #   config + clés API (chmod 600, JAMAIS dans la db)
```

- **Sauvegarde** = copier le dossier. **Duplication** = copier le dossier ailleurs.
- `DEVDASH_HOME` permet de tout déplacer (clé USB, dossier synchronisé
  OneDrive/Dropbox, autre machine) : `set DEVDASH_HOME=D:\usb\devdash` (Windows)
  ou `export DEVDASH_HOME=~/usb/devdash` (macOS/Linux).
- La config (clés API) est **séparée** de la base : une sauvegarde de `devdash.db`
  ne contient jamais de secret.
- Plusieurs projets indépendants = plusieurs `DEVDASH_HOME`, ou un seul dossier
  avec plusieurs projets (changement de projet actif sans perte de progression).

### Schéma de données (résumé, voir `devdash/core/schema.sql`)

| Table | Contenu |
|---|---|
| `meta` | paires clé/valeur (ex: `active_project_id`) |
| `projects` | slug, nom, description, moteur, couleur, statut, archivé |
| `features` | nom, statut (`open/proposed/done/rejected`), source (manuel/IA), confiance, raisonnement, feedback, `validated_at` (posé **uniquement** par un humain) |
| `tasks` | projet, parent (sous-tâches), titre, priorité, estimation, statut, tags (JSON), feature liée |
| `sessions` | projet, tâche, kind (focus/pause/custom), début/fin, durée, note |
| `log_entries` | devlog daté, lié à un projet/tâche, tags |
| `git_links` / `git_commits` / `git_sync_state` | dépôts liés, commits importés (lecture seule), tâche/feature parsée, branche |
| `events` | **bus d'événements append-only** : toute mutation y écrit une ligne ; la GUI interroge `events?since=` pour le temps réel |

### Sécurité (en bref)

- Serveur GUI **uniquement sur `127.0.0.1`**, avec vérification `Host` **et**
  `Origin` (anti-CSRF / anti-DNS-rebinding), `Content-Type: application/json`
  exigé sur les POST, plafond de body 1 Mo, `X-Content-Type-Options: nosniff`,
  erreurs 500 génériques (les détails sont logués côté serveur uniquement).
- Chemins statiques servis via `is_relative_to` (pas de traversée de dossier).
- `devdash.db` et `config.json` en `chmod 600` (best effort sous Windows).
- Git **strictement lecture seule** (`git log` / `git rev-parse`, subprocess en
  liste, jamais de shell). Les regex de `commit_patterns` sont appliquées sur un
  sujet tronqué (anti-ReDoS).
- Aucune clé API dans la DB, les événements, les logs ou les erreurs affichées.
- Risque résiduel assumé : `git link` peut lire l'historique de n'importe quel
  dépôt local (c'est son rôle) — la GUI est protégée contre les appels
  cross-site, mais ne lie que des dépôts de confiance.

## 6. Assistant IA (optionnel)

Désactivé par défaut (`ai_enabled = false`) : l'outil est **entièrement
fonctionnel** sans IA, et tout ce qui touche à l'IA disparaît du CLI et de la
GUI (seul l'onglet **Réglages** reste visible pour configurer l'IA).
Chaque fonction est activable individuellement via `ai_feature_flags`.

**Depuis la GUI** : onglet ⚙ Réglages → choisir le provider dans la liste
(local ou cloud), remplir modèle / URL / clé API / commande CLI, cocher les
fonctions actives, puis **Tester la connexion** (envoi d'un simple « OK »,
jamais de contexte projet) et **Enregistrer**. La clé saisie n'est jamais
renvoyée au client (champ en écriture seule : laisser vide = garder la clé
enregistrée).

**À la main** : `~/.devdash/config.json` (créé à la main ; `chmod 600` automatique à l'écriture) :

```json
{
  "lang": "fr",
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

### Providers disponibles (`ai_provider`)

| Provider | Cible | Cloud ? | Config requise |
|---|---|---|---|
| `ollama` | Ollama en local (`http://localhost:11434` par défaut, surcharge via `ai_base_url`) | non | `ai_model` ex. `"qwen3:8b"` |
| `local` | **Tout serveur local compatible OpenAI** : LM Studio (`:1234` par défaut), llama.cpp server (`:8080`), vLLM, text-generation-webui… | non | `ai_model` (+ `ai_base_url` si port exotique) — aucune clé requise |
| `cli` | **Toute IA locale en ligne de commande** (llama-cli, mistral.rs, `ollama run`…) : le prompt JSON part en stdin, la réponse revient en stdout, **sans shell** | non | `ai_cli_command` ex. `"llama-cli -m model.gguf"` |
| `anthropic` | **Claude** (API Anthropic, `https://api.anthropic.com` par défaut) | **oui** | `ai_api_key` (`sk-ant-…`) + `ai_model` ex. `"claude-sonnet-4-5"` |
| `mistral` / `openai` | Cloud (URLs par défaut) | **oui** | `ai_api_key` + `ai_model` |
| `openai_compat` | Tout endpoint cloud compatible OpenAI | oui, sauf si `ai_base_url` est en loopback | `ai_base_url` + `ai_api_key` + `ai_model` |
| `mock` | Provider déterministe sans réseau (tests/démo) | non | rien |

- `ai_context_max_chars` : taille max du contexte compact envoyé au modèle (défaut 6000).

- Un provider cloud = un **avertissement visuel** permanent dans la GUI
  (bandeau) et un avertissement imprimé en CLI : le contexte du projet part
  vers un service externe. La détection se base sur l'URL (loopback = local).
- **Aperçu du contexte** : `devdash ai context` (CLI) ou le bouton
  « Voir le contexte envoyé à l'IA » (GUI) montre **exactement** le résumé
  compact envoyé au modèle (`core/ai/context.py`, tronqué à 6000 caractères) —
  **sans rien envoyer**, même avec l'IA activée.
- **Protection des clés** : `config.json` vit dans `~/.devdash/` (hors dépôt),
  est écrit en `chmod 600`, la clé n'apparaît **jamais** dans la base, dans les
  événements, dans les logs ni dans les messages d'erreur (masquée `***`). Si
  vous pointez `DEVDASH_HOME` dans le dépôt, le `.gitignore` exclut
  `.devdash/`, `config.json`, `*.db`, `*.gguf`, `*secret*` — aucune clé API ne
  peut être commitée par accident (`git ls-files` reste clean).

### Langue / Language

Toute l'interface (CLI **et** GUI) est traduite. Une seule option dans
`config.json` :

```json
{ "lang": "fr" }
```

- `"fr"` (défaut) ou `"en"` — modifiable à chaud : chaque commande CLI relit la
  config, et la GUI la relit au chargement (endpoint `/api/i18n`).
- Seul le texte **affiché** est traduit. Les commandes CLI, les valeurs stockées
  (`todo`, `high`, `unity`…), les clés de `config.json` et la sortie `--json`
  restent en anglais et stables pour vos scripts.
- Le catalogue complet vit dans `devdash/core/i18n.py` (un dictionnaire par
  langue, ~350 clés) : ajouter une langue = ajouter un dictionnaire + une entrée
  dans `SUPPORTED_LANGS`.

Règles de sécurité : l'IA ne fait **jamais** d'action destructive ou écrite
sans confirmation explicite (`--apply` en CLI, clic de confirmation en GUI).
Le contexte envoyé à l'IA est un **résumé compact** construit par le core
(`core/ai/context.py`), pas un dump de la base. Une feature n'est **jamais**
marquée terminée automatiquement : le système propose, l'humain décide
(`feature done` manuel, ou `feature approve` d'une proposition IA ; un rejet
est enregistré comme feedback).

## 7. Architecture

```
devdash/
├── core/                 # logique métier, stdlib uniquement, testable seule
│   ├── db.py             #   connexion SQLite, WAL, migrations de schéma
│   ├── schema.sql        #   schéma (v4) documenté
│   ├── models.py         #   dataclasses Project/Task/Feature/Session/...
│   ├── config.py         #   DEVDASH_HOME + config.json (séparé des données)
│   ├── events.py         #   bus d'événements append-only (temps réel)
│   ├── projects.py / tasks.py / sessions.py / logs.py / features.py
│   ├── gitwatch.py       #   Git lecture seule + parsing des messages
│   ├── stats.py / status.py / demo.py
│   └── ai/               #   provider abstrait + ollama / local / cli / anthropic /
│                         #   mistral / openai / openai_compat / mock + context compact
│                         #   + assistant (context/ask/summarize/plan/verdict)
├── cli/                  # typer + rich ; ne fait que formater et forwarder au core
│   ├── main.py           #   point d'entrée, option globale --json
│   └── commands/         #   projects/task/session/log/git/feature/ai/stats/demo/gui/status
├── gui/                  # fenêtre native PyWebView + serveur HTTP 127.0.0.1 stdlib
│   ├── server.py         #   routes API GET/POST + watcher Git en arrière-plan
│   ├── api.py            #   tous les endpoints (mêmes fonctions core que le CLI)
│   ├── desktop.py        #   run(): fenêtre native, ou --browser
│   └── static/           #   index.html / app.js / styles.css (thème sombre)
│                         #   + vendor/chart.umd.min.js (Chart.js embarqué, hors-ligne)
└── tests/                # pytest (119 tests)
```

**Pourquoi Python** : stdlib riche (sqlite3, http.server, json, dataclasses),
déploiement trivial par `pip install -e .` sur les trois OS, PyWebView donne une
vraie fenêtre native sans embarquer Chromium (~200 Mo économisés vs Electron),
et le typage (dataclasses + `from __future__ import annotations`) reste propre.
Inconvénient assumé : deux dépendances CLI (typer/rich) et une dépendance GUI
(pywebview) — le cœur reste 100 % stdlib.

**Pourquoi fenêtre native plutôt que web app** : pas d'onglet navigateur à
surveiller, démarrage direct, contrôle total du cycle de vie, et la page reste
servie en local donc utilisable hors-ligne. RAM : un processus Python + le
WebView2 système (~30-60 Mo), bien moins qu'une app Electron.

## 8. Tests

```bash
pip install -e ".[test]"
PYTHONPATH=. python -m pytest -q   # 119 tests : core, CLI, sessions, logs, git, GUI,
                                   # features, IA, stats, status, review, sécurité
```

## 9. Limites volontaires

Pas de cloud, pas de compte, pas de synchro multi-appareils, pas de gestion
d'équipe, pas d'écriture Git, pas de notification email. L'IA est une couche
optionnelle : sans elle, rien ne manque.

**Pas de Docker, volontairement** : DevDash est local par conception — CLI et
fenêtre native sur la machine du dev, données SQLite dans `~/.devdash`, dépôts
git lus en local, serveur GUI lié à `127.0.0.1` uniquement. Un conteneur devrait
monter tous ces volumes (home, repos) : aucun bénéfice, que de la complexité.
La portabilité entre OS est déjà couverte par Python 3.11+ (install `pip`) et
par l'exécutable autonome (`scripts/build_exe.py`). Même pour faire tourner la
GUI en mode `--browser` sur un NAS, l'exécutable ou le venv s'y exécutent
directement, sans conteneur.
