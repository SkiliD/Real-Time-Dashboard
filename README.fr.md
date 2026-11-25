# DevDash

DevDash est un petit **tableau de bord personnel qui vit sur ton ordinateur** pour
suivre tes projets — dev de jeux, web, ou autre : tâches, temps de travail, notes et
ce que disent tes commits Git, au même endroit. Une **ligne de commande** pour les
actions rapides et une **application de bureau** pour le reste, toutes deux sur les
mêmes données. Tout reste sur ta machine : **pas de compte, pas de cloud, pas besoin
d'internet**.

> 🇬🇧 English version: [README.md](README.md)

## Que peut-il faire ?

- **Projets** — plusieurs en parallèle (Unity, Unreal, Godot, web…) ; connecte un
  dossier existant (`devdash projects connect <chemin>`) : moteur auto-détecté, dépôt
  git englobant lié automatiquement en lecture seule.
- **Tâches** — priorités, tags, estimations, sous-tâches, **kanban** glisser-déposer.
- **Minuteur de travail** — sessions focus/pause, Pomodoro optionnel, note de fin.
- **Devlog** — notes datées : décisions, idées, blocages.
- **Git, en lecture seule** — commits auto-liés aux tâches (`#42`, `feat: …`) ; les
  seules commandes distantes autorisées sont en lecture seule et ne tournent que si
  tu les demandes : `git fetch`, `git pull --ff-only` (fast-forward uniquement, jamais
  forcé), `git remote -v`. Onglet Git de la GUI : boutons Fetch / Pull, cases par
  fichier pour un diff combiné, rafraîchissement en direct. Un push est refusé par
  conception.
- **Stats** — temps, tâches terminées, série de jours, progression ; filtres par
  projet / période / groupement, avec graphiques.
- **Features** — regroupe des tâches ; marquée « terminée » **que par toi**.
- **Assistant IA optionnel** — modèles locaux (Ollama, LM Studio…) ou API cloud ;
  il n'écrit **jamais rien sans ton accord explicite**.

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
# Linux uniquement : la fenêtre native a aussi besoin de WebKit2GTK
# Debian/Ubuntu : sudo apt install python3-gi gir1.2-webkit2-4.1
# Fedora :        sudo dnf install webkit2gtk4.1 python3-gobject
# Arch :          sudo pacman -S webkit2gtk python-gobject
```

Sans écran (headless) : `devdash gui --browser` — la même appli dans ton navigateur.
Sans environnement virtuel : `pipx install .`

> **Windows — AppLocker/WDAC** : si le lanceur `devdash.exe` généré par pip est bloqué
> par une stratégie de contrôle d'application, passe par l'interpréteur :
> `.venv\Scripts\python.exe -m devdash gui` (un shim `devdash.bat` fonctionne aussi).

> **Windows — Defender/SmartScreen** : un `.exe` PyInstaller non signé peut être signalé
> comme suspect. Sans aucune commande : double-clic sur `DevDash.bat` pour lancer sans
> aucun exe, ou `Install-DevDash.bat` une fois pour créer un raccourci Bureau. Pour un
> `.exe` « propre » : signez-le avec un certificat de confiance (`scripts/sign_exe.ps1`),
> ou double-clic sur `scripts/Add-Defender-Exclusion.bat` une fois (clic droit →
> Exécuter en tant qu'administrateur) pour exclure le dossier de l'analyse Defender.

### Sans Python ? Fabrique un exécutable

```bash
pip install -e ".[build]"
devdash build        # -> dist/devdash/devdash.exe  (aussi : ⚙ Réglages → carte « Exécutable (.exe) »)
```
Options : `--onefile` (fichier unique), `--no-gui` (CLI seul), `--no-console` (exe
fenêtrée). Compile-le sur l'OS où tu vas l'utiliser — le résultat est un dossier
double-cliquable, prêt pour une clé USB.

> **SmartScreen Windows / antivirus** : l'exe n'est *pas signé*, le premier lancement
> peut afficher « Windows a protégé votre PC » → **Informations complémentaires →
> Exécuter quand même**. Seul un *certificat de signature de code de confiance*
> supprime l'avertissement pour de bon :
> `.\scripts\sign_exe.ps1 -ExePath "dist\devdash\devdash.exe"`.
> DevDash est 100 % local : pas de compte, pas de télémétrie, aucun appel réseau
> sauf un `git fetch` ou un provider IA que tu déclenches toi-même.

## Premiers pas

```bash
devdash demo seed                                  # données de démo pour explorer sans risque
devdash gui                                        # application de bureau
devdash projects connect "C:\chemin\vers\mon\projet"  # dossier existant : moteur + git auto-détectés
devdash task add "Dash du joueur" --tag gameplay --est 90
devdash session start --task 1
devdash session stop --note "ça marche, à peaufiner"
devdash log add "décision : input buffer de 0,15s sur le dash"
devdash stats week
```
Ajoute `--json` avant n'importe quelle commande pour une sortie lisible par machine.

## L'application de bureau

`devdash gui` ouvre une **vraie fenêtre native sans bordure OS** (pas un onglet
navigateur) : sa propre barre de titre (glisser pour déplacer, boutons
réduire/agrandir/fermer, double-clic pour agrandir), l'icône de l'app dans la **barre
des tâches / Alt-Tab**, et une mise en page qui s'adapte à toutes les tailles de
fenêtre. Onglets : **Tâches** (kanban), **Devlog**, **Git**, **Features**, **Stats**,
**Assistant IA** — tout en direct, y compris les commits Git (watcher en
arrière-plan). `devdash gui --browser` lance la même appli dans ton navigateur sur
`http://127.0.0.1:8765`.

## Commandes principales

| Tu veux… | Commande |
|---|---|
| projets | `devdash projects connect "<chemin>"` · `projects list` · `projects set-active <slug>` |
| tâches | `devdash task add "…" --tag gameplay --est 90` · `task list` · `task done 3` · `task start 3` |
| minuteur | `devdash session start --task 3 [--pomodoro]` · `devdash session stop --note "…"` |
| devlog | `devdash log add "…"` · `devdash log search "…"` |
| git | `devdash git status` · `git log` · `git fetch` · `git pull` (ff-only) · `git sync` |
| stats / résumé | `devdash status` · `devdash stats week` · `devdash stats all` |
| sauvegarde / export | `devdash backup` · `devdash export --out export.json` · `devdash import export.json` |
| mise à jour | `devdash update` (pull ff-only + deps) · `devdash update --check` |
| fabriquer l'exe | `devdash build` |
| connecter un agent IA (Claude Code…) | `devdash mcp` (extrait de config : `devdash mcp-config vscode`) |

📚 **Référence technique complète** — toutes les commandes, schéma SQL, hooks
Vibe/Claude Code, outils MCP, architecture, détails de sécurité :
[docs/README.detail.md](docs/README.detail.md)
Guide illustré de l'interface (visite guidée au premier lancement, kanban, sessions, git) : [docs/guide-utilisation.md](docs/guide-utilisation.md)

## Réglages (onglet ⚙)

Tout se règle depuis l'application : provider IA + test de connexion, **langue**
(FR/EN, effet immédiat), **thème** (auto/sombre/clair), durées Pomodoro, intervalle du
watcher Git, port de la GUI, patterns de commits, sauvegardes automatiques,
sauvegarde en un clic, cartes « Exécutable » et « Mise à jour ». Rien ne nécessite
d'éditer un fichier.

## Où vivent tes données

Un seul dossier : `~/.devdash/`

```
~/.devdash/
├── devdash.db     # toutes tes données (un seul fichier SQLite)
└── config.json    # réglages + clés API (lisible par toi seul)
```

Sauvegarde = copier ce dossier (ou `devdash backup`). Déplace tout avec la variable
d'environnement `DEVDASH_HOME`. La config est **séparée** des données exprès :
sauvegarder tes données n'inclut jamais tes clés API.

## Vie privée & sécurité, en clair

- L'application de bureau n'écoute que `127.0.0.1` et vérifie Host/Origin — un site
  web au hasard ne peut pas s'en servir.
- Git reste en lecture seule : DevDash lance `git log`, jamais `git commit`/`push`/etc.
  La seule écriture possible dans le dépôt est le bloc `.gitignore` balisé, sur ta
  demande explicite (`devdash git ignore`).
- Base et config lisibles par ton utilisateur seul ; les clés API n'apparaissent
  jamais dans les logs, les erreurs ni la base.
- L'IA ne reçoit qu'un court résumé du projet, jamais la base brute — vérifiable à tout
  moment avec `devdash ai context`.

## Langue

Toute l'interface (CLI **et** GUI) est traduite. Mets `"lang": "fr"` ou `"en"` dans
`config.json` — effet immédiat. Les commandes et les valeurs stockées restent en
anglais, pour que tes scripts continuent de marcher.

## Pour les développeurs

Un **cœur Python 100 % stdlib** (SQLite + bus d'événements) fait tout le travail ; la
CLI (typer/rich) et la GUI (petit serveur HTTP local + fenêtre native PyWebView) ne
sont que des habillages au-dessus — c'est pour ça qu'elles sont toujours d'accord.

```bash
PYTHONPATH=. python -m pytest -q    # 460+ tests
```

## Ce que DevDash n'est volontairement pas

Pas de cloud, pas de comptes, pas de fonctions d'équipe, pas de synchro
multi-appareils, pas d'email — et pas de Docker non plus : c'est un outil local, un
conteneur n'ajouterait que de la complexité. L'IA est une couche en plus, jamais un
prérequis.
