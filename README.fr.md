# DevDash

DevDash est un petit **tableau de bord personnel qui vit sur ton ordinateur**, pour
t'aider à suivre tes projets — dev de jeux, web, ou autre. Il montre tes tâches, le
temps que tu travailles, tes notes et ce que disent tes commits Git, au même endroit.

Il y a une **ligne de commande** pour les actions rapides et une **application de
bureau** pour le reste. Les deux partagent les mêmes données : ce que tu fais dans
l'une apparaît aussitôt dans l'autre.

Tout reste sur ta machine : **pas de compte, pas de cloud, pas besoin d'internet**.

> 🇬🇧 English version: [README.md](README.md)

---

## Que peut-il faire ?

- **Projets** : plusieurs en parallèle (Unity, Unreal, Godot, web…), un seul « actif »
  à la fois. Changer de projet ne perd ni ne mélange jamais rien. **Connecter un
  projet existant** : champ « dossier » dans la fenêtre « Nouveau projet » (ou
  `devdash projects connect <chemin>`) — le moteur est détecté automatiquement
  (Unity/Unreal/Godot/web via les fichiers marqueurs) et le dépôt git englobant
  est lié automatiquement en lecture seule. Depuis la barre latérale (bouton ✎
  au survol) : renommer, changer de technologie/couleur/statut, ou supprimer
  définitivement (avec confirmation).
- **Tâches** : to-do liste avec priorités, tags (`gameplay`, `ui`…), estimations,
  sous-tâches et recherche, affichée en **kanban glisser-déposer** — clic sur une
  carte pour l'éditer (titre, description, priorité, tags, parent, feature).
- **Minuteur de travail** : démarrage/arrêt de sessions (focus / pause / libre,
  durée personnalisée, mode Pomodoro 25+5 min par défaut). Chaque session peut se
  terminer par une note : ce que tu as fait, ce qui bloque.
- **Devlog** : des notes rapides et datées — décisions, idées, blocages.
  Cherchables, modifiables et supprimables. C'est ta mémoire long terme.
- **Git, en lecture seule** : lie un dépôt et DevDash affiche tes commits (branche,
  dernier commit, commits par jour) et les relie aux tâches (`#42`,
  `feat(dash): …`). Il ne fait que **lire** — il ne modifie jamais ton dépôt.
  Une seule exception, sur demande explicite : `devdash git ignore` (ou le bouton
  « Ajouter au .gitignore » dans l'onglet Git) écrit un **bloc balisé** dans le
  `.gitignore` de TON dépôt pour que les fichiers locaux de DevDash (base, config,
  backups, exports) ne soient jamais commités — idempotent, jamais dupliqué.
- **Stats** : temps travaillé, tâches terminées, série de jours, progression — avec
  **filtres** : par projet (actif / un projet / tous les projets), période (7 à 365 j),
  groupement (jour / semaine / mois), et graphique comparatif « temps par projet ».
- **Features** : regroupe des tâches en une feature. Une feature n'est marquée
  « terminée » **que par toi** — jamais automatiquement.
- **Assistant IA optionnel** : pose des questions sur ton projet (« où en suis-je ? »),
  résumé de la semaine, découpe d'une grosse tâche en sous-tâches. Fonctionne avec
  des modèles locaux (Ollama, LM Studio, toute IA locale) ou des API cloud (Claude,
  Mistral, OpenAI). Totalement optionnel — sans lui, rien ne manque.

## Installation

Il faut **Python 3.11 ou plus récent**.

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

Ou sans environnement virtuel : `pipx install .`

Alias pratique (optionnel) : `alias dd='devdash'` dans ton `~/.bashrc` / `~/.zshrc`
(`dd.bat` appelant `devdash %*` sous Windows).

### Sans Python ? Fabrique un exécutable

```bash
pip install -e ".[build]"
py -3 scripts/build_exe.py        # -> dist/devdash.exe (Windows) / dist/devdash (macOS/Linux)
```

Compile-le sur l'OS où tu vas l'utiliser. Le résultat tient en un seul fichier
double-cliquable — mets-le sur une clé USB si tu veux. (`--onedir` pour un dossier,
`--no-gui` pour un exécutable CLI seul, plus léger.)

## Premiers pas

```bash
devdash demo seed        # données de démo pour explorer sans risque
devdash gui              # ouvre l'application de bureau
devdash status           # résumé texte du projet actif
```

Avec un vrai projet :

```bash
devdash projects connect "C:\chemin\vers\mon\projet"   # dossier EXISTANT : moteur auto-détecté (Unity/Unreal/Godot/web), dépôt git auto-lié en lecture seule
# ou à la main :
devdash projects create "Aether Vale" --engine unity --set-active
devdash git link "C:\chemin\vers\mon\depot"
devdash task add "Dash du joueur" --priority high --tag gameplay --est 90
devdash session start --task 1
devdash session stop --note "le dash marche, à peaufiner"
devdash log add "décision : input buffer de 0,15s sur le dash"
devdash stats week
```

Ajoute `--json` avant n'importe quelle commande pour une sortie lisible par machine
(pour tes scripts).

## L'application de bureau

`devdash gui` ouvre une **vraie fenêtre native** (pas un onglet navigateur) : thème
sombre, fonctionne hors-ligne. Onglets : **Tâches** (kanban), **Devlog**, **Git**,
**Features**, **Stats** (graphiques), **Assistant IA** (seulement si l'IA est
activée). Tout se met à jour en direct — y compris les nouveaux commits Git, qu'un
watcher en arrière-plan relève toutes les 20 secondes.

`devdash gui --browser` fait la même chose dans ton navigateur, sur
`http://127.0.0.1:8765`.

## Mémo de commandes

| Tu veux… | Commande |
|---|---|
| voir les projets / changer d'actif | `devdash projects list` · `devdash projects set-active aether-vale` · `devdash projects show aether-vale` · `devdash projects update aether-vale --status beta` · `devdash projects archive aether-vale` · `devdash projects delete aether-vale` |
| connecter un dossier existant | `devdash projects connect "C:\\games\\aether-vale"` (moteur + git détectés automatiquement) |
| ajouter / lister / finir une tâche | `devdash task add "Dash" --tag gameplay --est 90` · `devdash task list` · `devdash task done 3` · `devdash task start 3` · `devdash task cancel 3` · `devdash task reopen 3` · `devdash task edit 3 --priority high` · `devdash task show 3` · `devdash task delete 3` |
| filtrer les tâches | `devdash task list --status todo --tag ui --feature dash` |
| lancer / arrêter le minuteur | `devdash session start --task 3 --pomodoro` · `devdash session stop --note "…"` · `devdash session now` · `devdash session list --week` |
| écrire dans le devlog | `devdash log add "idée : dash aérien chaîné"` · `devdash log search "dash"` · `devdash log recent` · `devdash log edit 2 --tags idée` · `devdash log delete 2` |
| voir l'activité Git | `devdash git status` · `devdash git log --task 3` · `devdash git sync` · `devdash git link <chemin>` · `devdash git unlink` · `devdash git ignore` |
| gérer les features (toi seul les valides) | `devdash feature add dash` · `devdash feature list` · `devdash feature show dash` · `devdash feature link dash 3 4` · `devdash feature unlink dash 3` · `devdash feature done dash` · `devdash feature approve dash` · `devdash feature reject dash` · `devdash feature edit dash --name air-dash` · `devdash feature delete dash` · `devdash feature reopen dash` |
| voir tes stats | `devdash status` · `devdash stats today` · `devdash stats week` · `devdash stats month` · `devdash stats streak` · `devdash stats tags` · `devdash stats all` |
| tout sauvegarder | `devdash backup --out D:\backups` (zip horodaté) |
| exporter / réimporter les données | `devdash export --out export.json` · `devdash import export.json` |
| de l'aide IA | `devdash ai ask "où en suis-je ?"` · `devdash ai plan "système de combat" --apply` · `devdash ai verify 3` · `devdash ai context` |
| connecter un agent IA (Claude Code…) | `devdash mcp` — voir ci-dessous |

## Mettre à jour après un `git pull`

```bash
devdash update           # pull --ff-only + réinstall des dépendances (install git)
devdash update --check   # juste vérifier si une mise à jour existe
devdash update status    # mode d'installation + infos git (sans réseau)
devdash version          # version + hash git du checkout
```
Depuis la GUI : onglet **⚙ Réglages → Mise à jour** (vérifier / mettre à jour en un clic,
la GUI doit être relancée ensuite). Install pip/pipx : `pip install --upgrade devdash` /
`pipx upgrade devdash`. Exécutable autonome : le remplacer ou le reconstruire
(`py -3 scripts/build_exe.py`). Aucun force-push, et l'update est refusé si le dépôt
a des changements non commités.

## Réglages (onglet ⚙)

Tout se règle depuis l'application : provider IA (local ou cloud) avec test de
connexion, **langue de l'interface** (français/anglais, effet immédiat), durées
**Pomodoro**, intervalle du **watcher Git**, **port de la GUI**, **patterns de
commits** liant commits et tâches, et un bouton **« Sauvegarder maintenant »**
(zip horodaté du dossier de données). Rien ne nécessite d'éditer un fichier.

## L'assistant IA (optionnel)

Désactivé par défaut. Le plus simple : onglet **⚙ Réglages** de l'application,
choisis un provider (local ou cloud), remplis modèle/clé, puis « Tester la
connexion » et « Enregistrer » — sans éditer de fichier. Ou à la main dans
`~/.devdash/config.json` :

```json
{ "ai_enabled": true, "ai_provider": "ollama", "ai_model": "qwen3:8b" }
```

Choisis un provider :

- **`ollama`** — un modèle local via [Ollama](https://ollama.com). Rien ne quitte ton PC.
- **`local`** — tout serveur local compatible API OpenAI (LM Studio, llama.cpp…).
- **`cli`** — n'importe quelle IA locale en ligne de commande (`ai_cli_command: "llama-cli -m model.gguf"`).
- **`anthropic` / `mistral` / `openai` / `openai_compat`** — des API cloud ; tu fournis
  `ai_api_key`. DevDash affiche alors un avertissement visible, car un résumé de ton
  projet est envoyé à ce service.

Deux habitudes de sécurité intégrées : l'IA ne reçoit qu'un **court résumé** de ton
projet (jamais la base brute) — vérifie-le quand tu veux avec `devdash ai context` —
et elle **n'écrit jamais rien sans ton accord** (`--apply` en CLI, bouton de
confirmation dans l'app). Les clés API ne vivent que dans `config.json` (un fichier
lisible par toi seul), jamais dans les données, les logs ni les messages d'erreur.

## Où vivent tes données

Tout tient dans **un seul dossier** : `~/.devdash/`

```
~/.devdash/
├── devdash.db     # toutes tes données (un seul fichier SQLite)
└── config.json    # réglages + clés API (lisible par toi seul)
```

- **Sauvegarde** = copier ce dossier (ou `devdash backup`).
- Déplace tout avec la variable d'environnement `DEVDASH_HOME` (clé USB, dossier
  synchronisé, autre ordinateur).
- La config est **séparée** des données exprès : sauvegarder tes données n'inclut
  jamais tes clés API.

## Vie privée & sécurité, en clair

- L'application de bureau n'écoute que `127.0.0.1` (ta machine, pas le réseau) et
  vérifie que les requêtes viennent bien d'elle — un site web au hasard ne peut pas
  s'en servir.
- Git est en lecture seule : DevDash lance `git log`, jamais `git commit`/`push`/etc.
- La base et le fichier de config sont lisibles par ton utilisateur seul.
- Tes clés API n'apparaissent jamais dans les logs, les erreurs ni la base.

## Connecter DevDash à un agent IA (MCP)

`devdash mcp` démarre un petit serveur MCP local pour que des agents comme
**Claude Code** puissent interroger DevDash (statut, tâches, stats…) et ajouter des
tâches/logs/sessions.

- Claude Code : ajoute `.mcp.json` à la racine de ton projet :
  ```json
  { "mcpServers": { "devdash": { "command": "devdash", "args": ["mcp"] } } }
  ```
- Vibe : `~/.vibe/config.toml` → `[mcp_servers.devdash]` avec `command = "devdash"`, `args = ["mcp"]`.

Mêmes règles partout : aucune action destructive, Git en lecture seule, et une
feature n'est jamais marquée terminée par un agent — seulement par toi.

Pour rafraîchir les commits Git après ceux faits par un agent, ajoute un hook
post-tool qui lance `devdash git sync` (snippets prêts à copier dans
[docs/README.detail.md](docs/README.detail.md)).

## Langue

Toute l'interface (CLI **et** GUI) est traduite. Mets `"lang": "fr"` ou `"en"` dans
`config.json` — effet immédiat. Les commandes et les valeurs stockées restent en
anglais, pour que tes scripts continuent de marcher.

## Pour les développeurs

L'architecture en une phrase : un **cœur Python 100 % stdlib** (SQLite + bus
d'événements) fait tout le travail ; la CLI (typer/rich) et la GUI (petit serveur HTTP
local + fenêtre native PyWebView) ne sont que des habillages au-dessus — c'est pour
ça qu'elles sont toujours d'accord.

```bash
PYTHONPATH=. python -m pytest -q    # 228+ tests
```

En option : `pip install playwright && playwright install chromium` active un vrai
test navigateur de la GUI (il se désactive proprement sinon).

## Ce que DevDash n'est volontairement pas

Pas de cloud, pas de comptes, pas de fonctions d'équipe, pas de synchro
multi-appareils, pas d'email — et pas de Docker non plus : c'est un outil local, un
conteneur n'ajouterait que de la complexité. L'IA est une couche en plus, jamais
un prérequis.

---

📚 **Pour aller plus loin** (référence technique complète : toutes les commandes,
schéma SQL, hooks Vibe/Claude Code, MCP, architecture détaillée) :
[docs/README.detail.md](docs/README.detail.md) (🇫🇷) /
[docs/README.detail.en.md](docs/README.detail.en.md) (🇬🇧)
