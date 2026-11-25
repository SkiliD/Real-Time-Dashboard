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
- La GUI est une **vraie fenêtre native** (PyWebView / WebView2 sous Windows), pas une web app : pas d'onglet navigateur, RAM maîtrisée, aucun lien avec Internet requis (Chart.js est embarqué localement). La fenêtre est **sans bordure OS** (`frameless`) avec une barre de titre maison (glisser pour déplacer, boutons réduire / agrandir / fermer, double-clic sur la barre pour agrandir) ; en mode navigateur (`--browser`) la barre est masquée et le rendu est inchangé. La fenêtre native embarque l'icône de l'app (`devdash/gui/icon.ico`, multi-tailles) pour la barre des tâches et Alt-Tab ; l'exécutable `devdash.exe` l'utilise aussi comme icône de fichier.

---

## 1. Installation

Prérequis : **Python 3.11+** (CI : testé avec 3.11 et 3.13 sur 3 OS). Aucune dépendance pour le cœur.

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

Linux uniquement — la fenêtre native (PyWebView) a aussi besoin du paquet système WebKit2GTK :
```bash
# Debian/Ubuntu : sudo apt install python3-gi gir1.2-webkit2-4.1
# Fedora :        sudo dnf install webkit2gtk4.1 python3-gobject
# Arch :          sudo pacman -S webkit2gtk python-gobject
```
macOS ne demande rien de plus. Sans écran (headless) : `devdash gui --browser`.

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

PyInstaller fabrique un exécutable double-cliquable (dossier onedir par défaut, `--onefile` pour un fichier unique) :

```bash
pip install -e ".[build]"            # ajoute pyinstaller
py -3 scripts/build_exe.py           # -> build dossier dist/devdash/devdash.exe (défaut, plus discret pour les antivirus)
py -3 scripts/build_exe.py --onefile # build fichier unique dist/devdash.exe (plus pratique à partager)
py -3 scripts/build_exe.py --no-gui  # exe CLI seul (plus petit, sans pywebview)
py -3 scripts/build_exe.py --no-console  # exe fenêtrée (pas de console) pour un double-clic GUI
# SmartScreen (exe non signé) : « Informations complémentaires > Exécuter quand même ».
# Pour supprimer l'avertissement avec un certificat de confiance :
#   .\scripts\sign_exe.ps1 -ExePath "dist\devdash\devdash.exe"
# Le build embarque les métadonnées de version Windows (scripts/version_info.txt) et un
# manifeste asInvoker/DPI (scripts/app.manifest) ; UPX est désactivé.
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
Des endpoints en lecture seule exposent aussi la liste des tags (`GET /api/tags`),
les dépôts Git liés (`GET /api/git/repos`) et les fichiers de `DEVDASH_HOME`
(`GET /api/files` : sauvegardes/exports, jamais `config.json`), confinés au
répertoire home local. `POST /api/features/propose` (lecture seule aussi :
propositions de l’IA en attente de validation humaine). `GET /api/session/current` (session en cours) et `GET /api/tasks/tree` (arborescence avec rollups) sont aussi exposés comme **API publique** (tests / consommateurs externes), même si la GUI lit la session via `/api/status` et construit l'arbre côté client. `POST /api/fs/pick` ouvre le sélecteur de dossier natif
de l'OS sur le bureau et renvoie le chemin choisi (ou `null` si annulé) — la
GUI ne fait que pré-remplir le champ chemin ; connecter un dossier ou lier un
dépôt reste une action explicite de l'utilisateur.

Onglets : **Tâches** (kanban avec sous-tâches empilables façon Notion/Trello :
glisser une carte sur une autre = sous-tâche, barre de progression x/y sur le
parent, bouton « ＋ sous-tâche », bascule empilé/à plat dans la barre d'outils ;
pour les gros projets : colonnes repliables (clic sur l'en-tête, persisté),
plafond de 40 cartes/ colonne avec bouton « ▸ voir N de plus », bouton
« ✖ filtres » pour tout effacer d'un coup ; case « Corbeille » : les suppressions (tâches, devlog, features) sont **douces** par défaut — restaurables, définitives en 2 clics depuis la corbeille),
**Devlog**, **Git**, **Features**, **Stats**
(graphiques Chart.js embarqués + encadré « Estimé vs réel » (estimation vs temps réellement passé, par tâche et agrégé) + bouton « ⬇ Rapport hebdo (.md) »), **Timeline** (fil d'activité du projet — tâches, sessions, devlog, commits git, features — du plus récent au plus ancien, avec filtre par type), **Assistant IA** (visible uniquement si l'IA
est activée ; l'historique du chat est **persisté** en base et effaçable depuis l'onglet), **Réglages** (toujours visible : provider IA local ou cloud +
test de connexion + fonctions actives, mais aussi langue de l'interface,
Pomodoro, intervalle du watcher Git, port de la GUI, patterns de commits,
thème (sombre / clair / **auto** = suit l'OS), sauvegarde **auto** (tous les N jours, conserve les K derniers zip), et bouton de sauvegarde zip).

## 4. Référence CLI

Sortie lisible par humain par défaut ; ajoutez `--json` (option globale, avant
la commande) pour une sortie machine.

| Commande | Exemple | Description |
|---|---|---|
| `projects list` | `devdash projects list` | liste, `*` = actif |
| `projects create` | `devdash projects create "Aether Vale" --engine unity --status development --color #7c5cff --set-active` | `--engine`, `--status`, `--color`, `--description`, `--set-active` |
| `projects connect <path>` | `devdash projects connect "C:\jeux\aether-vale" --set-active` | **connecte un dossier existant** : moteur auto-détecté (`ProjectSettings/ProjectVersion.txt` → unity, `*.uproject` → unreal, `project.godot` → godot, `package.json` → web), nom suggéré depuis le dossier, racine git trouvée en remontant l'arborescence et **auto-liée en lecture seule** (commits importés immédiatement) ; `--no-git` pour refuser, `--engine` pour forcer. Le dossier est **mémorisé** sur le projet (colonne `path`) |
| `projects open [ref]` | `devdash projects open` | ouvre le **dossier connecté** du projet (actif par défaut) dans l'explorateur de fichiers — GUI : boutons 📂 dans la sidebar et l'en-tête ; MCP : `devdash_projects_open` ; API : `POST /api/projects/open`. Chemin stocké côté serveur, ouvert en argv sans shell, 400 propre s'il n'existe plus |
| `projects set-active <ref>` | `devdash projects set-active aether-vale` | **sans perte** : simple pointeur, les autres projets gardent tout |
| `projects show/update/archive` | `devdash projects show aether-vale` ; `devdash projects update aether-vale --status beta` ; `devdash projects archive aether-vale` | détail, édition, archivage. **Suppression dure** : `devdash projects delete aether-vale` (confirmation requise) ou depuis la GUI (sidebar ✎ → Supprimer, 2 clics) via `POST /api/projects/delete` — cascade complète (tâches, sessions, devlog, commits liés), le projet actif est réassigné |
| `task add` | `devdash task add "Dash" -p high -t gameplay -t combat --est 90` | `-p` projet (défaut: actif), `--parent` sous-tâche, `--feature <id>` (id numérique, ex. `--feature 3`) pour lier à une feature dès la création |
| `task list` | `devdash task list -s todo --priority high --tag ui --feature dash --limit 20` | filtres statut/priorité/tag/feature + `--limit` pour les gros projets |
| `task tree` | `devdash task tree` | arborescence : sous-tâches empilées sous le parent + progression (x/y) |
| `task show <id>` | `devdash task show 3` | détail d'une tâche + ses sous-tâches |
| `task start/done/cancel/reopen <id>` | `devdash task done 3` | changement de statut |
| `task edit <id>` | `devdash task edit 3 --priority critical --parent 7` / `--unparent` | édition ; `--parent N` empile la tâche sous #N (anti-cycle), `--unparent` la détache |
| `task delete <id>` | `devdash task delete 3 --yes` | suppression **douce** par défaut (corbeille, sous-tâches suivies) ; `--permanent` = définitive |
| `task restore <id>` | `devdash task restore 3` | restaure une tâche de la corbeille (avec ses sous-tâches) |

Les projets ont aussi une **couleur** (qui thème l'interface quand le projet est actif) et des **langages** (`--lang csharp --lang gdscript`, modifiables dans la GUI) : `devdash projects create "Mon Jeu" --engine unity --lang csharp`.
| `session start` | `devdash session start --task 3 --kind focus --pomodoro` | minuteur interactif ; `--background` pour rendre la main, `--pomodoro` = après le focus, propose de lancer la pause automatiquement |
| `session stop` | `devdash session stop --note "fait: X, bloqué: Y"` | durée + note de fin |
| `session now` / `session list` | `devdash session list --week` | session en cours / historique (`--today`, `--week`, `-t <id tâche>`) |
| `log add` | `devdash log add "idée: dash aérien chaîné" --tag idea` | entrée de devlog |
| `log recent` / `log list` / `log search` | `devdash log search "cooldown"` | journal daté, recherchable (`list` = alias de `recent`) |
| `log edit` / `log delete` / `log restore <id>` | `devdash log delete 5 --yes` ; `devdash log restore 5` | édition ; suppression **douce** par défaut (corbeille), `--permanent` = définitive |
| `git link <path>` | `devdash git link "C:\repo"` | lie le projet actif à un dépôt (lecture seule) |
| `git unlink` | `devdash git unlink` | délie le dépôt du projet actif (les commits en cache sont conservés) |
| `git sync` | `devdash git sync` | importe les nouveaux commits (tous projets si `-p` omis) |
| `git log` | `devdash git log --task 3` | commits liés à une tâche (`--unparsed` = non reconnus) |
| `git status` | `devdash git status` | branche, dernier commit, commits/jour |
| `git run <cmd>` | `devdash git run "log --oneline -5"` | exécute une commande git **en lecture seule** dans le dépôt lié (GUI : ligne terminal dans l'onglet Git ; MCP : `devdash_git_run`). Liste blanche de sous-commandes (`log`, `diff`, `status`, `branch -a`…), pas de shell, options interdites refusées, confinée au dépôt lié, avec timeout — les commandes d'écriture (`push`, `commit`, `branch -d`…) sont refusées avec une explication |
| `git fetch` | `devdash git fetch` | met à jour les refs distantes du dépôt lié (lecture distante ; GUI : bouton « ⇩ Fetch » ; MCP : `devdash_git_remote`) |
| `git pull` | `devdash git pull` | avance la branche en **fast-forward uniquement** (`--ff-only`, jamais de fusion forcée ; GUI : « ⇩ Pull » ; MCP : `devdash_git_remote`) |
| `git remote` | `devdash git remote` | liste les remotes configurés du dépôt lié (`remote -v`, lecture seule ; MCP : `devdash_git_remote`) |
| `git ignore` | `devdash git ignore [--repo <path>]` | écrit/rafraîchit le **bloc balisé** `# >>> devdash >>>` dans le `.gitignore` du dépôt lié : `.devdash/`, `devdash.db*`, snapshots, `devdash-backup-*.zip`, `devdash-export-*.json`, et `<home>/config.json` si `DEVDASH_HOME` est dans le dépôt (clés API). Idempotent (jamais dupliqué), contenu existant préservé. **Seul fichier que DevDash écrit dans votre dépôt, et seulement sur cette commande explicite** — l'historique git reste intouché |
| `feature add/list/show` | `devdash feature add dash` ; `devdash feature list` ; `devdash feature show dash` | création/liste/détail (progression, tâches liées) |
| `feature propose <name>` | `devdash feature propose dash` | enregistre une proposition (utilisé par l'assistant IA ; ne marque JAMAIS terminée) |
| `feature link/unlink` | `devdash feature link dash 3 4` ; `devdash feature unlink dash 3` | attacher/détacher des tâches |
| `feature done/reopen` | `devdash feature done dash` ; `devdash feature reopen dash` | **validation manuelle** d'une feature (seul un humain peut la fermer) |
| `feature approve/reject` | `devdash feature reject dash --feedback "pas assez testé"` | approuver/rejeter une proposition IA (le rejet est mémorisé) |
| `feature edit/delete` | `devdash feature edit dash --name air-dash` ; `devdash feature delete dash --yes` | renommage (unique par projet) / suppression **douce** par défaut (corbeille, restaurable via `feature restore`) — `--permanent` = définitive (les tâches sont détachées, jamais supprimées) ; aussi éditable depuis la GUI (✎/🗑 sur la carte feature) via `POST /api/features/update` et `POST /api/features/delete` |
| `status` | `devdash status` ; `devdash status --watch` | instantané temps réel (tâches, session, streak, git) |
| `stats today/week/month/streak/tags/all` | `devdash stats week` | temps, tâches faites, streak, répartition par tag |
| `stats estimates` | `devdash stats estimates` | **estimé vs réel** : estimation de chaque tâche vs minutes réellement passées (sessions), + agrégat (total estimé/réel, % , nb au-dessus/en dessous) |
| `timeline` | `devdash timeline --limit 50 [--type task]` | **fil d'activité** du projet (bus d'événements : tâches, sessions, devlog, commits git…), du plus récent au plus ancien ; GUI : onglet Timeline, MCP : `devdash_timeline` |
| `report` | `devdash report --period week [--out rapport.md] [--ai]` | **rapport markdown** semaine/mois 100 % local (temps par jour, tâches faites, commits liés, devlog, estimé vs réel) ; `--ai` ajoute le résumé IA si configuré ; GUI : bouton « ⬇ Rapport hebdo » dans Stats (`POST /api/report`), MCP : `devdash_report` |
| `ai context` | `devdash ai context` | **affiche le contexte exact** qui serait envoyé à l'IA — rien n'est envoyé, même IA activée |
| `ai ask` | `devdash ai ask "où en suis-je ?"` | chat (si IA activée) |
| `ai summarize` | `devdash ai summarize --period week` | résumé de la semaine |
| `ai plan` | `devdash ai plan "système de combat" --apply` | découpe en sous-tâches (`--apply` = seule écriture possible) |
| `ai suggest` | `devdash ai suggest [--apply]` | **analyse tout le projet** et propose des tickets (tâches, priorités, estimations, raisons) — lecture seule, création uniquement via `--apply` (consentement explicite). GUI : bouton « Analyser le projet et proposer des tickets » dans l'onglet IA, cases à cocher + bouton de création ; API : `POST /api/ai/suggest` (propose, n'écrit jamais) puis `POST /api/ai/suggest/apply` (champs whitelistés, re-validés, max 20/appel) |
| `ai verdict` | `devdash ai verdict dash` | proposition de validation IA (jamais automatique) |
| `ai verify <id>` | `devdash ai verify 3 [--apply]` | verdict IA sur une tâche (objectif atteint ?) — lecture seule, sauf `--apply` explicite |
| `demo seed` | `devdash demo seed` | données de démo (`--force` si des projets existent) |
| `backup` | `devdash backup --out D:ackups` | **zip horodaté** de `~/.devdash` (db + config), snapshot SQLite cohérent même GUI ouverte |
| `export` | `devdash export --out export.json` | **export JSON complet** (toutes les tables) pour archive/migration/inspection — lecture seule |
| `import` | `devdash import export.json [--force]` | restaure un export JSON dans la base (refusé si la base n’est pas vide, sauf `--force`) |
| `mcp` | `devdash mcp` | serveur **MCP** (JSON-RPC stdio) pour connecter DevDash à un agent IA externe |
| `mcp-config` | `devdash mcp-config vscode [--write]` | imprime (ou écrit) le extrait de config MCP pour VS Code, Cursor, Vibe ou Claude Desktop |
| `gui` | `devdash gui` | fenêtre graphique native |
| `version` | `devdash version` | version + hash git du checkout |
| `update` | `devdash update [--check] [--no-reinstall]` · `update check` · `update status` | **auto-mise à jour** : install git → `git pull --ff-only` (jamais de force, refus si arbre sale) + `pip install -e .` pour rafraîchir les dépendances ; install pip/pipx → affiche la bonne commande ; exécutable → rebuild. `restart_needed` retourné. GUI : carte « Mise à jour » dans Réglages (`GET /api/update/status`, `POST /api/update/check`, `POST /api/update`) |
| `build` | `devdash build [--onefile] [--no-gui] [--no-console]` | **construit l’exécutable autonome** (`dist/devdash/devdash.exe`, lançable sans commande — double-clic) via PyInstaller (requis : `pip install -e ".[build]"`, build 100 % local sans réseau) ; `--onefile` = fichier unique, `--no-gui` = exe CLI seul, `--no-console` = exe fenêtré. GUI : carte « Exécutable (.exe) » dans Réglages (`POST /api/build/run`, `GET /api/build/status`). Exe non signé : SmartScreen « Exécuter quand même » au premier lancement, et AppLocker/WDAC peut bloquer un exe non signé — signer avec `scripts/sign_exe.ps1` |

### Conventions de commits Git

Les messages sont reliés aux tâches/features par des regex configurables
(`commit_patterns` dans `config.json`) :

- `#42` → tâche n°42
- `task-42: ...` → tâche n°42
- `feat(dash): ...` → feature « dash »

Les commits non reconnus restent listés à part (`git log --unparsed`).

**Onglet Git façon GitHub Desktop** (tout en lecture seule) : sélecteur de
branches locales (en choisir une affiche ses derniers commits), panneau
« Fichiers modifiés » (`git status --porcelain` + `diff --numstat` : badge de
statut + compteurs +/−, clic = diff du fichier), bouton « 📂 Ouvrir le dépôt »
dans l'explorateur, terminal `git$` pour commandes en liste blanche
(`POST /api/git/run`, `/api/git/changes`, `/api/git/branches`, `/api/git/open`).
Boutons « ⇩ Fetch » / « ⇩ Pull » (`POST /api/git/remote` : liste blanche `fetch`,
`pull --ff-only`, `remote -v` — **toute écriture distante est refusée**, règle du
projet) et, dans le panneau « Fichiers modifiés », des cases à cocher + « Tout » +
« Diff sélection » (`POST /api/git/diff` : diff combiné en lecture seule des
fichiers choisis, chemins validés et confinés au dépôt, option « index » pour
`--cached`) ; la liste se rafraîchit en temps réel (watcher git + polling 1,5 s).
**Détection de ticket lié** : quand des changements non commités existent,
`/api/git/changes` renvoie `linked_task` (session en cours sur la tâche en
priorité, sinon dernier commit parsé `#42`/`task-42:`) et la GUI affiche une
bannière « 🧩 changements liés à #N » avec « ✔ Marquer #N terminée » (**double
clic de confirmation** — la complétion n'est jamais affirmée sans un clic humain)
plus « 🤖 Vérifier avec l'IA » (verdict lecture seule ; un verdict positif
affiche le bouton « Approuver et marquer terminée »).
**Aucune opération d'écriture Git** : DevDash ne fait que des commandes git en lecture seule (`git log` / `git rev-parse` pendant le sync ; `git run` applique une liste blanche lecture seule ; `git remote` n'accepte que `-v`, `git pull` exige `--ff-only` — voir ci-dessus).

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
[[mcp_servers]]
name = "devdash"
command = "devdash"
args = ["mcp"]
```

**VS Code** (Copilot / Cline / toute extension compatible MCP) — `.vscode/mcp.json`
dans le workspace :
```json
{ "servers": { "devdash": { "type": "stdio", "command": "python", "args": ["-m", "devdash", "mcp"] } } }
```
(ou l'exécutable venv/autonome en `command`). Une commande imprime/écrit le
extrait pour chaque éditeur supporté :
```bash
devdash mcp-config vscode            # imprime le snippet pour .vscode/mcp.json
devdash mcp-config vscode --write    # l'écrit/fusionne directement
devdash mcp-config cursor|vibe|claude-desktop
```

Outils exposés (23) : `devdash_status`, `devdash_projects_list`, `devdash_projects_open` (ouvre le dossier connecté dans l'explorateur), `devdash_tasks_list`,
`devdash_tasks_tree`, `devdash_context` (**bundle de contexte complet en un appel** —
projet, progression, arborescence des tâches ouvertes avec rollups, devlog récent,
snapshot git avec commits récents, stats du jour — pour qu'une IA dans VS Code suive
le projet sans chaîner les appels), `devdash_task_add` (param `parent` = sous-tâche),
`devdash_task_set_status`, `devdash_task_verify`,
`devdash_session_start/stop`, `devdash_log_add/search`, `devdash_stats`,
`devdash_git_status/log/run/remote` (`remote` = `fetch` / `pull --ff-only` / `remote -v`, écritures distantes refusées), `devdash_timeline` (fil d'activité), `devdash_report` (rapport markdown semaine/mois),
`devdash_features_list`, `devdash_ai_ask`,
`devdash_ai_suggest` (si l’IA est activée).
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
- **Sauvegarde auto** : au démarrage de la GUI, un zip `devdash-backup-*.zip` est
  écrit tous les N jours (réglage « Données », 0 = désactivé) et seuls les K
  derniers sont conservés.

### Schéma de données (résumé, voir `devdash/core/schema.sql`)

| Table | Contenu |
|---|---|
| `meta` | paires clé/valeur (ex: `active_project_id`) |
| `projects` | slug, nom, description, moteur, couleur, statut, archivé |
| `features` | nom, statut (`open/proposed/done/rejected`), source (manuel/IA), confiance, raisonnement, feedback, `validated_at` (posé **uniquement** par un humain), `deleted_at` (corbeille) |
| `tasks` | projet, parent (sous-tâches), titre, priorité, estimation, statut, tags (JSON), feature liée, `deleted_at` (corbeille) |
| `sessions` | projet, tâche, kind (focus/pause/custom), début/fin, durée, note |
| `log_entries` | devlog daté, lié à un projet/tâche, tags, `deleted_at` (corbeille) |
| `git_links` / `git_commits` / `git_sync_state` | dépôts liés, commits importés (lecture seule), tâche/feature parsée, branche |
| `events` | **bus d'événements append-only** : toute mutation y écrit une ligne ; la GUI interroge `events?since=` pour le temps réel |
| `ai_messages` | historique du chat IA (persisté entre les sessions, élagué à 500 entrées) |

### Sécurité (en bref)

- Serveur GUI **uniquement sur `127.0.0.1`**, avec vérification `Host` **et**
  `Origin` (anti-CSRF / anti-DNS-rebinding), `Content-Type: application/json`
  exigé sur les POST, plafond de body 1 Mo, `X-Content-Type-Options: nosniff`,
  **`X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` sur TOUTES les
  réponses** (anti-clickjacking — un `<iframe>` de notre propre origine aurait
  le bon Host), erreurs 500 génériques (les détails sont logués côté serveur uniquement).
- Chemins statiques servis via `is_relative_to` (pas de traversée de dossier).
- `devdash.db` et `config.json` en `chmod 600` (best effort sous Windows) ;
  `config.json` est écrit **atomiquement** (tmp + `os.replace`) sous un lock
  (serveur GUI threadé : pas de perte de mise à jour ni de fichier corrompu).
- Fichiers temporaires (snapshot DB, zip de backup, export JSON) créés via
  `tempfile.mkstemp` (`O_EXCL`) : nom imprévisible, pas de course symlink.
- `git ignore` refuse d'écrire à travers un symlink `.gitignore`.
- Détection « cloud » de l'IA par **résolution DNS** (`getaddrinfo` + `ipaddress
  .is_loopback`) : `127.0.0.1.evil.com` n'est PAS classé local ; hôte
  irrésolvable = traité comme distant (fail-closed).
- Le contexte envoyé au modèle IA est délimité (`=== DÉBUT/FIN DES DONNÉES
  PROJET ===`) avec instruction explicite de ne jamais suivre les instructions
  qu'il contient (anti-prompt-injection ; les messages de commits viennent d'un
  dépôt peut-être cloné d'une source hostile).
- Sorties de subprocess (git, pip, sélecteur de dossier) décodées en **UTF-8**
  (`errors="replace"`) : plus de mojibake selon la locale Windows (cp1252).
- `sysopen.open_in_explorer` n'ouvre que des répertoires (défense en profondeur).
- **Mise à jour** : `git pull --ff-only` + `pip install -e .` exécutent le code
  de `origin` SANS vérification de signature — modèle de confiance explicite :
  ne mettre à jour que depuis le dépôt officiel, sur un réseau de confiance.
- Git **strictement lecture seule** : subprocess en liste, jamais de shell, les
  métacaractères de shell ne peuvent rien exécuter ; `devdash git run` applique
  en plus une liste blanche de sous-commandes + un filtrage **fail-closed** des
  arguments (options longues refusées par préfixe dont `--no-index`/`--force*`/
  `--git-dir=`, formes courtes attachées `-dfoo`/`-mfoo`, `reflog delete/expire`,
  et tout chemin absolu), confine au dépôt lié (`-C`) et applique un timeout.
  Les regex de `commit_patterns` sont appliquées sur un sujet tronqué (anti-ReDoS).
- Champ `engine` d'un projet restreint à `[a-z0-9-]{1,40}` (rendu HTML échappé
  côté GUI en plus) : pas de XSS stocké via un nom de moteur exotique.
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
(local ou cloud), choisir le **modèle dans la liste de presets** du provider
(ex. Claude Opus/Sonnet, GPT-5, Mistral Large… ; « Personnalisé… » pour tout
autre), remplir URL / clé API / commande CLI si besoin, cocher les fonctions
actives, puis **Tester la connexion** (envoi d'un simple « OK », jamais de
contexte projet) et **Enregistrer**. La clé saisie n'est jamais renvoyée au
client (champ en écriture seule : laisser vide = garder la clé enregistrée).

**Providers** : Ollama / serveur local compatible OpenAI / CLI locale (rien ne
quitte la machine) · Anthropic, Mistral, OpenAI (cloud, clé requise) ·
**OpenRouter** (cloud, UNE seule clé pour tous les grands modèles — Claude,
GPT, Gemini, DeepSeek, Qwen… — modèle à choisir dans la liste déroulante) ·
endpoint custom compatible OpenAI. Le bandeau d'avertissement cloud s'affiche
pour tout provider non-loopback.

**Contexte envoyé au modèle** (borné par `ai_context_max_chars`) : projet actif +
progression, tâches ouvertes, **arborescence des sous-tâches avec rollups**,
session en cours, minutes du jour + streak, devlog récent, features
ouvertes/proposées, et le snapshot git (branche, commits récents avec leurs
tâches liées).

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
  langue, ~970 clés) : ajouter une langue = ajouter un dictionnaire + une entrée
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
│   ├── schema.sql        #   schéma (v10) documenté
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
│   ├── api.py            #   façade Api = mixins api_*.py (mêmes fonctions core que le CLI)
│   ├── api_common.py     #   ApiError + helpers partagés
│   └── api_<domaine>.py   #   endpoints par domaine (projects, tasks, git, ai…)
│   ├── desktop.py        #   run(): fenêtre native, ou --browser
│   └── static/           #   index.html / app.js / styles.css (thème sombre)
│                         #   + vendor/chart.umd.min.js (Chart.js embarqué, hors-ligne)
└── tests/                # pytest (460+ tests)
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
PYTHONPATH=. python -m pytest -q   # 460+ tests : core, CLI, sessions, logs, git, GUI,
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
