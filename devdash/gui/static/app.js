/* DevDash GUI frontend.
 * Vanilla JS, no build. Local API over HTTP + /api/events polling for live
 * refresh (any CLI mutation refreshes the GUI too).
 */
"use strict";

const $ = (sel) => document.querySelector(sel);

const api = {
  get: (p) => fetch(p).then(async (r) => {
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || r.statusText); // a 4xx/5xx must not kill the UI silently
    return d;
  }),
  post: (p, body) =>
    fetch(p, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(async (r) => {
      const d = await r.json().catch(() => ({})); // non-JSON body: keep the real status text
      if (!r.ok) throw new Error(d.error || r.statusText);
      return d;
    }),
};

const state = {
  projects: [],
  active: null, // slug
  tasks: [],
  allTags: [], // unfiltered tag list for the filter select (H1: was rebuilt from filtered tasks)
  session: null,
  logs: [],
  logQuery: "",
  filterTag: "",
  taskQuery: "",
  filterPrio: "",
  subtaskView: "stacked", // "stacked" = Notion-style under the parent card; "flat" = classic kanban
  expandedParents: {},    // {taskId: true} — which parent cards show their subtask stack
  collapsed: {},          // {status: true} — collapsed kanban columns (persisted)
  colCaps: {},            // {status: n} — per-column "voir plus" raised caps
  gitTaskFilter: "",
  gitSeenHash: null, // newest commit hash at last Git-tab visit (activity dot baseline)
  tlSeenId: null,    // max timeline event id at last Timeline-tab visit (activity dot baseline)
  lastLoadAt: 0,     // Date.now() of the last successful full load ("maj il y a X s")
  editingTaskId: null,
  editingFeatId: null, // feature currently open in the edit modal
  featOpen: null, // feature name currently expanded
  lastEventId: 0,
  tab: "tasks",
  gitStatus: null,
  gitCommits: [],
  gitUnparsed: false,
  gitBranches: null,   // {branches: [...], current} from /api/git/branches
  gitChanges: null,    // {files: [...], insertions, deletions} from /api/git/changes
  gitChangesOpen: false,
  gitSelFile: null,    // path of the changed file whose diff is shown
  gitSelFiles: {},     // multi-selected changed files {path: true} for the combined diff
  features: [],
  statsFilters: { project: "", days: "14", group: "day" }, // "" = active project
  ai: { enabled: false },
  chat: [],
  aiBusy: null, // {label, started} while an AI call is in flight (typing bubble + locked inputs)
  stats: null,
  estimates: null,   // GET /api/stats/estimates — estimate vs actual per task
  timeline: [],      // GET /api/timeline — activity feed (event bus)
  tlFilter: "",      // timeline entity filter ("" = all)
  sidebarCollapsed: false, // slim rail mode (persisted)
  showTrash: false,   // tasks tab: corbeille view
  tourDone: false,    // first-launch guided tour already seen (persisted in localStorage)
  logTrash: false,    // log tab: corbeille view
  featTrash: false,   // features tab: corbeille view
  aiCfg: null,        // settings form model (GET /api/ai/config)
  aiProviders: [],    // provider catalog (GET /api/ai/providers)
  settings: null,     // GET /api/settings (general block + home)
  settingsDirty: false, // true while the user is editing the form (don't wipe it on poll)
  editingSlug: null,  // project currently open in the edit modal
};

/* ---------- i18n: language from config.json `lang`, served by /api/i18n ----------
 * Stored values (statuses/priorities/...) stay English lowercase in the API;
 * display labels come from the catalog: T("prio." + t.priority), etc. */
let I18N = { lang: "fr", messages: {} };
function T(key, vars) {
  let s = (I18N.messages && I18N.messages[key]) || key;
  if (vars) for (const k in vars) s = s.split("{" + k + "}").join(String(vars[k]));
  return s;
}
/* theme: "dark" (default) | "light" — persisted in config.json, mirrored in
   localStorage so the saved theme applies before first paint (see index.html) */
/* theme: "dark" | "light" | "auto" — "auto" follows the OS (prefers-color-scheme)
   and re-applies live when the OS switches while the GUI is open */
function resolveTheme(t) {
  if (t === "auto") return (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
  return t === "light" ? "light" : "dark";
}
function onSysThemeChange() {
  let raw = null;
  try { raw = localStorage.getItem("devdash-theme"); } catch (e) { /* ignore */ }
  if (raw === "auto") document.documentElement.dataset.theme = resolveTheme("auto");
}
function applyTheme(t) {
  const raw = t || "auto";
  try { localStorage.setItem("devdash-theme", raw); } catch (e) { /* private mode: config still applies on load */ }
  document.documentElement.dataset.theme = resolveTheme(raw);
  if (window.matchMedia) {
    const mql = matchMedia("(prefers-color-scheme: light)");
    if (mql.removeEventListener) mql.removeEventListener("change", onSysThemeChange);
    if (raw === "auto" && mql.addEventListener) mql.addEventListener("change", onSysThemeChange);
  }
}

// slim-rail sidebar (persisted like the theme/tab in localStorage)
function applySidebar() {
  document.body.classList.toggle("sidebar-collapsed", !!state.sidebarCollapsed);
}

function applyI18n() {
  document.documentElement.lang = I18N.lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    // LEAF elements only: textContent on a parent would destroy its nested
    // elements (e.g. #btn-git-changes contains <span id="git-changes-count">)
    if (el.children.length === 0) el.textContent = T(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = T(el.dataset.i18nPh); });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = T(el.dataset.i18nTitle); });
}

// serialize reloads: a poll and a user action must never interleave two loadAll()
let _loadChain = Promise.resolve();
function loadAll() {
  const p = _loadChain.then(realLoadAll);
  _loadChain = p.catch(() => {}); // keep the chain alive after a failure
  return p;
}

async function realLoadAll() {
  const p = await api.get("/api/projects");
  state.projects = p.projects;
  state.active = p.active;
  // all panel loads are independent: run them concurrently instead of 9 sequential round-trips
  // settings/AI-config/update are only needed on the settings tab: skip the
  // 3 extra round-trips on every poll when the user is elsewhere
  const settingsLoads = state.tab === "settings"
    ? [loadAiConfig(), loadSettings(), loadUpdateStatus()]
    : [];
  // stats/estimates are heavy (aggregations): only poll them on the stats tab
  const statsLoads = state.tab === "stats" ? [loadStats(), loadEstimates()] : [];
  await Promise.all([
    loadTasks(), loadTags(), loadSessions(), loadTaskMinutes(), loadLogs(), loadGit(),
    loadFeatures(), loadAiStatus(), loadTimeline(), ...statsLoads, ...settingsLoads,
    // /api/status also carries the running session: no need for a second call
    api.get("/api/status").then((s) => { state.status = s; state.session = s.session || null; }),
  ]);
  render();
  state.lastLoadAt = Date.now(); // drives the "maj il y a X s" header hint
}

async function loadTasks() {
  const q = new URLSearchParams({ all: "1" });
  if (state.filterTag) q.set("tag", state.filterTag);
  if (state.filterPrio) q.set("priority", state.filterPrio);
  if (state.taskQuery) q.set("q", state.taskQuery);
  if (state.showTrash) q.set("deleted", "1"); // corbeille view
  state.tasks = (await api.get("/api/tasks?" + q.toString())).tasks;
}

async function loadTags() {
  // unfiltered list of the ACTIVE project: the filter select must keep showing
  // every tag even while one is selected (otherwise you can never switch directly)
  state.allTags = (await api.get("/api/tags")).tags;
}

async function loadSessions() {
  state.recentSessions = (await api.get("/api/sessions?limit=8")).sessions;
}

// per-task total minutes for the card badges: one aggregated query instead of
// shipping hundreds of session rows on every poll
async function loadTaskMinutes() {
  try {
    state.timeByTask = (await api.get("/api/sessions/minutes")).minutes;
  } catch {
    state.timeByTask = {};
  }
}

async function loadLogs() {
  const q = new URLSearchParams({ limit: "200" });
  if (state.logQuery) q.set("q", state.logQuery);
  if (state.logTrash) q.set("deleted", "1"); // corbeille view
  state.logs = (await api.get("/api/logs?" + q.toString())).entries;
}

async function loadGit() {
  try {
    const prevRepo = state.gitStatus ? state.gitStatus.repo_path : null;
    state.gitStatus = await api.get("/api/git/status");
    if ((state.gitStatus.repo_path || null) !== (prevRepo || null)) state.gitSelFiles = {};
    const q = new URLSearchParams({ limit: "100" });
    if (state.gitUnparsed) q.set("unparsed", "1");
    if (state.gitTaskFilter) q.set("task", state.gitTaskFilter);
    state.gitCommits = (await api.get("/api/git/commits?" + q.toString())).commits;
    state.gitError = null;
    // GitHub-Desktop-style extras (all read-only): branches + changed files.
    // They spawn 4 git subprocesses server-side, so only poll them on the git tab
    if (state.gitStatus.linked && state.tab === "git") {
      const [br, ch] = await Promise.all([
        api.post("/api/git/branches", {}).catch(() => null),
        api.post("/api/git/changes", {}).catch(() => null),
      ]);
      state.gitBranches = br && br.ok ? br : null;
      state.gitChanges = ch && ch.ok ? ch : null;
    } else {
      state.gitBranches = null;
      state.gitChanges = null;
    }
  } catch (e) {
    state.gitStatus = { linked: false };
    state.gitCommits = [];
    state.gitBranches = null;
    state.gitChanges = null;
    state.gitError = e.message; // show the real error, not a fake "no repo linked"
  }
}

async function loadFeatures() {
  try {
    const q = new URLSearchParams();
    if (state.featTrash) q.set("deleted", "1"); // corbeille view
    state.features = (await api.get("/api/features" + (q.toString() ? "?" + q : ""))).features;
  } catch {
    state.features = [];
  }
  // keep the quick-add feature select in sync
  const sel = $("#new-task-feature");
  if (sel) sel.innerHTML = `<option value="">${T("gui.task.none")}</option>` +
    (state.features || []).map((f) => `<option value="${f.id}">⚑ ${escapeHtml(f.name)}</option>`).join("");
}

async function loadStats() {
  try {
    const f = state.statsFilters;
    const q = new URLSearchParams({ days: f.days, group: f.group });
    if (f.project) q.set("project", f.project); // "" -> active project (server default), "all" -> aggregated
    state.stats = await api.get("/api/stats?" + q.toString());
    state.lastStatsError = null;
    renderStatsFilters();
  } catch (e) {
    state.stats = null;
    // an empty stats tab with no explanation is confusing: toast the error,
    // but only when it CHANGES (the poll retries every 1.5 s — no toast spam)
    if (state.lastStatsError !== e.message) {
      state.lastStatsError = e.message;
      showError(e.message);
    }
  }
}

async function loadEstimates() {
  try {
    const q = new URLSearchParams();
    if (state.statsFilters.project) q.set("project", state.statsFilters.project);
    state.estimates = await api.get("/api/stats/estimates?" + q.toString());
  } catch {
    state.estimates = null;
  }
}

async function loadTimeline() {
  try {
    const q = new URLSearchParams({ limit: "150" });
    if (state.tlFilter) q.set("type", state.tlFilter);
    state.timeline = (await api.get("/api/timeline?" + q.toString())).events;
  } catch {
    state.timeline = [];
  }
}

function renderStatsFilters() {
  const f = state.statsFilters;
  const sel = $("#stats-project");
  sel.innerHTML =
    `<option value="">${T("gui.stats.project_active")}</option>` +
    state.projects.filter((p) => !p.archived).map((p) =>
      `<option value="${escapeHtml(p.slug)}">${escapeHtml(p.name)}</option>`).join("") +
    `<option value="all">${T("gui.stats.all_projects")}</option>`;
  sel.value = f.project;
  const days = $("#stats-days");
  days.innerHTML = [7, 14, 30, 90, 365]
    .map((n) => `<option value="${n}">${T("gui.stats.days_n", { n })}</option>`).join("");
  days.value = f.days;
  $("#stats-group").value = f.group;
}

async function loadAiStatus() {
  try {
    state.ai = await api.get("/api/ai/status");
  } catch {
    state.ai = { enabled: false };
  }
  // AI fully optional: hide the tab entirely when disabled
  $("#tab-ai-btn").classList.toggle("hidden", !state.ai.enabled);
  // settings: when the assistant is OFF, collapse its card to the enable toggle
  // (the rest of the form is useless until it is on — and the toggle stays so the
  // user can turn it back on from the GUI)
  const aiBody = $("#ai-config-body");
  if (aiBody) aiBody.classList.toggle("hidden", !state.ai.enabled);
  const banner = $("#ai-banner");
  if (state.ai.enabled) {
    banner.classList.remove("hidden");
    banner.textContent = "";
    const dot = document.createElement("span"); // real state marker: local vs cloud scope
    dot.className = "ai-dot " + (state.ai.cloud ? "cloud" : "local");
    banner.appendChild(dot);
    banner.appendChild(document.createTextNode(state.ai.cloud
      ? T("gui.ai_banner", { provider: state.ai.provider, model: state.ai.model || "-" })
      : T("gui.ai_banner_local", { provider: state.ai.provider, model: state.ai.model || "-" })));
  } else {
    banner.classList.add("hidden");
  }
}

/* ---------- settings tab: configure the AI (local or cloud) from the GUI ---------- */

async function loadAiConfig() {
  try {
    const [cfg, prov] = await Promise.all([
      api.get("/api/ai/config"), api.get("/api/ai/providers"),
    ]);
    state.aiCfg = cfg;
    state.aiProviders = prov.providers;
    // never overwrite the form while the user is typing in it
    if (!state.settingsDirty) fillAiForm();
  } catch { /* keep the form as-is on error */ }
}

function fillAiForm() {
  const cfg = state.aiCfg;
  if (!cfg) return;
  $("#cfg-enabled").checked = !!cfg.ai_enabled;
  const sel = $("#cfg-provider");
  sel.innerHTML = (state.aiProviders || [])
    .map((p) => `<option value="${p.id}">${T("prov." + p.id)}</option>`).join("");
  sel.value = cfg.ai_provider || "ollama";
  fillModelPresets(cfg.ai_model || "");
  $("#cfg-base-url").value = cfg.ai_base_url || "";
  $("#cfg-api-key").value = ""; // write-only: a stored key is never sent back to the client
  $("#cfg-api-key-set").classList.toggle("hidden", !cfg.api_key_set);
  $("#cfg-cli").value = cfg.ai_cli_command || "";
  $("#cfg-context-max").value = cfg.ai_context_max_chars || DEFAULT_AI_CONTEXT_MAX;
  const fl = cfg.ai_feature_flags || {};
  $("#flag-chat").checked = !!fl.chat;
  $("#flag-summarize").checked = !!fl.summarize;
  $("#flag-plan").checked = !!fl.plan;
  $("#flag-suggest").checked = !!fl.suggest;
  $("#flag-verdict").checked = !!fl.feature_verdict;
  $("#flag-task-verdict").checked = !!fl.task_verdict;
  $("#cfg-path").textContent = T("gui.set.config_path", { path: cfg.config_path || "" });
  updateProviderFields();
}

function currentProviderMeta() {
  return (state.aiProviders || []).find((p) => p.id === $("#cfg-provider").value) || {};
}

// current model value: preset select, or the free-text field when "custom"/no presets
function currentModelValue() {
  const sel = $("#cfg-model");
  if (sel.classList.contains("hidden")) return $("#cfg-model-custom").value;
  return sel.value === "__custom__" ? $("#cfg-model-custom").value : sel.value;
}

// populate the model dropdown from the provider's preset list; providers without
// presets (local servers) get a plain free-text field instead
function fillModelPresets(keep) {
  const meta = currentProviderMeta();
  const sel = $("#cfg-model");
  const custom = $("#cfg-model-custom");
  const models = meta.models || [];
  if (!models.length) {
    sel.classList.add("hidden");
    custom.classList.remove("hidden");
    custom.value = keep || "";
    return;
  }
  sel.classList.remove("hidden");
  sel.innerHTML = models.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}</option>`).join("") +
    `<option value="__custom__">${T("gui.set.model_custom")}</option>`;
  if (keep && models.some((m) => m.id === keep)) {
    sel.value = keep;
    custom.classList.add("hidden");
  } else if (keep) { // stored model is not a preset of this provider -> custom
    sel.value = "__custom__";
    custom.value = keep;
    custom.classList.remove("hidden");
  } else {
    custom.classList.add("hidden");
  }
}

function updateProviderFields() {
  const meta = currentProviderMeta();
  $("#field-model").classList.toggle("hidden", !meta.needs_model);
  $("#field-base-url").classList.toggle("hidden", !meta.needs_base_url);
  $("#field-api-key").classList.toggle("hidden", !meta.needs_key);
  $("#field-cli").classList.toggle("hidden", !meta.needs_cli_command);
  $("#cfg-provider-desc").textContent = meta.id ? T("prov." + meta.id + ".desc") : "";
  $("#cfg-base-url").placeholder = meta.default_base_url || T("gui.set.base_url_ph");
  const cloud = meta.scope === "cloud";
  $("#cfg-cloud-warn").classList.toggle("hidden", !cloud);
  $("#cfg-local-note").classList.toggle("hidden", cloud);
}

function readAiForm() {
  const flags = {
    chat: $("#flag-chat").checked,
    summarize: $("#flag-summarize").checked,
    plan: $("#flag-plan").checked,
    suggest: $("#flag-suggest").checked,
    feature_verdict: $("#flag-verdict").checked,
    task_verdict: $("#flag-task-verdict").checked,
  };
  return {
    ai_enabled: $("#cfg-enabled").checked,
    ai_provider: $("#cfg-provider").value,
    ai_model: currentModelValue(),
    ai_base_url: $("#cfg-base-url").value,
    ai_api_key: $("#cfg-api-key").value, // empty = keep the stored key
    ai_cli_command: $("#cfg-cli").value,
    ai_context_max_chars: parseInt($("#cfg-context-max").value, 10),
    ai_feature_flags: flags,
  };
}

async function saveAiConfig() {
  const r = await api.post("/api/ai/config", readAiForm());
  state.aiCfg = r.config;
  state.settingsDirty = false;
  fillAiForm();
  $("#cfg-status").textContent = T("gui.set.saved");
  await loadAiStatus(); // show/hide the AI tab + cloud banner immediately
}

/* ---------- general settings (language, pomodoro, git watcher, port, commit patterns) ---------- */

const LANG_LABELS = { fr: "Français", en: "English" };

async function loadSettings() {
  try {
    state.settings = await api.get("/api/settings");
    if (!state.settingsDirty) {
      fillSettingsForm();
      applyTheme(state.settings.general && state.settings.general.theme);
    }
  } catch { /* keep the form as-is on error */ }
}

function fillSettingsForm() {
  const s = state.settings;
  if (!s) return;
  const g = s.general;
  const sel = $("#cfg-lang");
  sel.innerHTML = (g.langs || ["fr", "en"])
    .map((l) => `<option value="${l}">${LANG_LABELS[l] || l}</option>`).join("");
  sel.value = g.lang || "fr";
  $("#cfg-pomo-focus").value = g.pomodoro_focus_min;
  $("#cfg-pomo-break").value = g.pomodoro_break_min;
  $("#cfg-git-watch").value = g.git_watch_interval_sec;
  $("#cfg-port").value = g.gui_port;
  $("#cfg-theme").value = g.theme || "auto";
  $("#cfg-patterns").value = (g.commit_patterns || []).join("\n");
  $("#cfg-auto-backup-days").value = g.auto_backup_days ?? 1;
  $("#cfg-auto-backup-keep").value = g.auto_backup_keep ?? 5;
  $("#set-home").textContent = s.home || "";
  $("#set-last-backup").textContent = g.last_auto_backup
    ? T("gui.set.last_backup", { at: g.last_auto_backup.slice(0, 19).replace("T", " ") })
    : T("gui.set.last_backup_none");
}

function readGeneralForm() {
  return {
    lang: $("#cfg-lang").value,
    pomodoro_focus_min: parseInt($("#cfg-pomo-focus").value, 10),
    pomodoro_break_min: parseInt($("#cfg-pomo-break").value, 10),
    git_watch_interval_sec: parseInt($("#cfg-git-watch").value, 10),
    gui_port: parseInt($("#cfg-port").value, 10),
    theme: $("#cfg-theme").value,
    commit_patterns: $("#cfg-patterns").value.split("\n").map((s) => s.trim()).filter(Boolean),
    auto_backup_days: parseInt($("#cfg-auto-backup-days").value, 10),
    auto_backup_keep: parseInt($("#cfg-auto-backup-keep").value, 10),
  };
}

async function saveAllSettings() {
  const gen = readGeneralForm();
  const ai = readAiForm();
  // a cleared number field parses to NaN: refuse it loudly instead of silently
  // sending null (= "no change") and letting the user believe it was saved as 0
  for (const [key, sel] of [["pomodoro_focus_min", "#cfg-pomo-focus"], ["pomodoro_break_min", "#cfg-pomo-break"],
                            ["git_watch_interval_sec", "#cfg-git-watch"], ["gui_port", "#cfg-port"]]) {
    if (Number.isNaN(gen[key])) { showError(T("gui.err.invalid_number", { field: sel })); return; }
  }
  if (Number.isNaN(ai.ai_context_max_chars)) { showError(T("gui.err.invalid_number", { field: "#cfg-context-max" })); return; }
  const body = { ...gen, ...ai };
  const r = await api.post("/api/settings", body);
  state.settings = r.settings;
  state.aiCfg = r.settings.ai;
  state.settingsDirty = false;
  applyTheme(r.settings.general && r.settings.general.theme); // theme applies hot
  renderStats._sig = null; // chart colors follow the theme
  if (state.tab === "stats") renderStats();
  fillSettingsForm();
  fillAiForm();
  $("#cfg-status").textContent = T("gui.set.saved");
  const gs = $("#general-status");
  if (gs) gs.textContent = T("gui.set.saved");
  await loadAiStatus(); // show/hide the AI tab + cloud banner immediately
  // language applies hot: reload the catalog and retranslate the static labels
  if (body.lang && body.lang !== I18N.lang) {
    try {
      I18N = await api.get("/api/i18n");
      applyI18n();
      // dynamic panels render through T() behind a data-only sig cache: force a
      // full re-render so every label switches language immediately
      for (const k in _panelSig) delete _panelSig[k];
      renderStats._sig = null;
      render();
      fillAiForm(); // provider labels are translated too
    } catch { /* next load will pick it up */ }
  }
}

async function doBackup() {
  const st = $("#backup-status");
  st.textContent = "";
  const r = await api.post("/api/backup", {});
  st.textContent = T("gui.set.backup_done", { path: r.path, kb: Math.max(1, Math.round(r.bytes / 1024)) });
}

async function doExport() {
  const st = $("#backup-status");
  st.textContent = "";
  const r = await api.post("/api/export", {});
  st.textContent = T("gui.set.export_done", { path: r.path, rows: r.rows });
}

/* ---------- self-update (settings tab) ---------- */

/* small motion helpers — every animation they trigger is disabled by the global
   prefers-reduced-motion rule in styles.css */
// two-step confirm shared by every destructive button: first click arms
// (label swap + auto-disarm after `ms`), second click runs. Returns true when
// the click was only the arming one (caller must return early).
function armOnce(btn, { confirmKey, resetKey, ms = ARM_MS, cls = null, titleKey = null, titleParams = null, onArm = null, onReset = null }) {
  const lbl = (k, p) => (k && k.includes(".") ? T(k, p) : k); // i18n key vs raw label (emoji)
  if (btn.dataset.armed === "1") { btn.dataset.armed = ""; return false; }
  btn.dataset.armed = "1";
  btn.textContent = lbl(confirmKey, titleParams);
  if (titleKey) btn.title = T(titleKey, titleParams);
  if (cls) btn.classList.add(cls);
  if (onArm) onArm();
  setTimeout(() => {
    btn.dataset.armed = "";
    btn.textContent = lbl(resetKey, titleParams);
    if (cls) btn.classList.remove(cls);
    if (onReset) onReset();
  }, ms);
  return true;
}

function setBtnBusy(btn, busy, label) {
  if (busy) {
    btn.dataset.label = btn.textContent; // restore on release
    btn.classList.add("loading");
    btn.disabled = true;
    btn.innerHTML = `<span class="spin"></span>${label}`;
  } else {
    btn.classList.remove("loading");
    btn.disabled = false;
    btn.textContent = btn.dataset.label || label;
    delete btn.dataset.label;
  }
}
function popStatus(el) { // (re)start the status reveal animation
  el.classList.remove("pop");
  void el.offsetWidth;
  el.classList.add("pop");
}

function syncUpdRunBtn() {
  // the "update now" button only exists once an update was actually DETECTED
  // (by the check button or by the last fetch) — never shown speculatively
  const btn = $("#btn-upd-run");
  const show = !!(state.upd && state.upd.update_available);
  if (show && btn.classList.contains("hidden")) {
    btn.classList.remove("hidden");
    btn.classList.remove("pop"); void btn.offsetWidth; btn.classList.add("pop");
  } else if (!show) {
    btn.classList.add("hidden");
  }
}

async function loadUpdateStatus() {
  try {
    state.upd = await api.get("/api/update/status");
    const u = state.upd;
    $("#upd-info").textContent = T("gui.upd.info", {
      version: u.version || "?", mode: u.mode || "?", head: u.head || "-",
    });
    syncUpdRunBtn();
  } catch { /* leave as-is */ }
}

async function checkUpdate() {
  const st = $("#upd-status");
  const btn = $("#btn-upd-check");
  st.textContent = "";
  setBtnBusy(btn, true, T("gui.upd.checking"));
  try {
    const r = await api.post("/api/update/check", {});
    if (r.error) st.textContent = T("gui.upd.fail", { error: r.error });
    else {
      state.upd = { ...(state.upd || {}), ...r };
      syncUpdRunBtn(); // show/hide "update now" from the detection result
      if (r.update_available) {
        st.textContent = T("gui.upd.available", { local: r.local, remote: r.remote, behind: r.behind });
        // finite signal (3 shakes, then stops): an update is available
        const run = $("#btn-upd-run");
        run.classList.remove("nudge"); void run.offsetWidth; run.classList.add("nudge");
      } else st.textContent = T("gui.upd.uptodate", { head: r.local || "-" });
    }
    popStatus(st);
  } catch (e) {
    st.textContent = T("gui.upd.fail", { error: e.message });
    popStatus(st);
  } finally {
    setBtnBusy(btn, false);
  }
}

async function runUpdate() {
  const st = $("#upd-status");
  const btn = $("#btn-upd-run");
  // two-step confirm (armOnce): first click arms, second runs
  if (armOnce(btn, { confirmKey: "gui.upd.confirm", resetKey: "gui.upd.update", ms: ARM_LONG_MS, onArm: () => { st.textContent = ""; } })) return;
  setBtnBusy(btn, true, T("gui.upd.running"));
  st.textContent = T("gui.upd.running");
  popStatus(st);
  $("#upd-progress").classList.add("on");
  try {
    const r = await api.post("/api/update", { reinstall: true, confirm: true });
    st.textContent = r.ok
      ? T("gui.upd.done", { head: r.head || "-" })
      : T("gui.upd.fail", { error: r.error || "?" });
    popStatus(st);
    if (r.ok && state.upd) { state.upd.update_available = false; syncUpdRunBtn(); }
    await loadUpdateStatus();
  } catch (e) {
    st.textContent = T("gui.upd.fail", { error: e.message });
    popStatus(st);
  } finally {
    setBtnBusy(btn, false);
    $("#upd-progress").classList.remove("on");
  }
}

/* ---------- build the standalone exe (settings tab) ---------- */
async function runBuild() {
  const btn = $("#btn-build");
  const st = $("#build-status");
  const log = $("#build-log");
  // two-step confirm (armOnce): first click arms, second runs
  if (armOnce(btn, { confirmKey: "gui.build.confirm", resetKey: "gui.build.btn", ms: ARM_LONG_MS })) return;
  setBtnBusy(btn, true, T("gui.build.running"));
  $("#build-progress").classList.add("on");
  st.textContent = T("gui.build.running");
  log.classList.add("hidden");
  try {
    const r = await api.post("/api/build/run", { onefile: $("#build-onefile").checked, confirm: true });
    if (!r.started) { st.textContent = T("gui.build.fail", { error: r.error || "?" }); return; }
    // the build runs in a background thread server-side: poll its status
    for (let i = 0; i < 1000; i++) {
      await new Promise((res) => setTimeout(res, 2000));
      const s = await api.get("/api/build/status");
      if (s.log_tail) { log.textContent = s.log_tail; log.classList.remove("hidden"); }
      if (!s.running) {
        st.textContent = s.ok
          ? T("gui.build.done", { exe: s.exe || "-" })
          : T("gui.build.fail", { error: s.error || "?" });
        popStatus(st);
        break;
      }
    }
  } catch (e) {
    st.textContent = T("gui.build.fail", { error: e.message });
    popStatus(st);
  } finally {
    setBtnBusy(btn, false);
    $("#build-progress").classList.remove("on");
  }
}

async function doDemoSeed() {
  const btn = $("#btn-demo");
  const st = $("#backup-status");
  if (armOnce(btn, { confirmKey: "gui.set.demo_confirm", resetKey: "gui.set.demo_btn" })) return;
  btn.textContent = T("gui.set.demo_btn");
  const r = await api.post("/api/demo/seed", { force: true });
  const n = Object.values(r).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0);
  st.textContent = T("gui.set.demo_done", { n });
  await loadAll();
}

async function testAiConfig() {
  const st = $("#cfg-status");
  st.textContent = "";
  try {
    // dry run with the form values (not saved): sends only a fixed "OK" prompt,
    // never project context
    const r = await api.post("/api/ai/test", readAiForm());
    st.textContent = T("gui.set.test_ok", { provider: r.provider, model: r.model || "-", reply: r.reply });
  } catch (e) {
    st.textContent = T("gui.set.test_fail", { msg: e.message });
  }
}

/* ---------- rendering ---------- */

/* Render each panel only when its input signature changed: the 1.5 s poll must
 * not rebuild the whole DOM (it stole keyboard focus, closed open <select>s and
 * interrupted an in-progress kanban drag). */
const _panelSig = {};
function renderPanel(name, data, fn) {
  const sig = JSON.stringify(data);
  if (_panelSig[name] === sig) return;
  _panelSig[name] = sig;
  fn();
}

function maxTlId() {
  return (state.timeline || []).reduce((m, e) => Math.max(m, e.id || 0), 0);
}

// activity dots on the Git / Timeline tabs: shown only for events that arrived
// AFTER the last visit (first poll just sets the baseline, so history is quiet)
function updateTabDots() {
  const newest = state.gitCommits && state.gitCommits[0];
  if (state.gitSeenHash == null) state.gitSeenHash = newest ? newest.hash : "";
  const gitNew = state.tab !== "git" && !!newest && newest.hash !== state.gitSeenHash;
  const bg = $("#badge-git");
  if (bg) { bg.classList.toggle("hidden", !gitNew); bg.title = T("gui.badge.git_new"); }
  if (state.tlSeenId == null) state.tlSeenId = maxTlId();
  const tlNew = state.tab !== "timeline" && maxTlId() > state.tlSeenId;
  const bt = $("#badge-timeline");
  if (bt) { bt.classList.toggle("hidden", !tlNew); bt.title = T("gui.badge.tl_new"); }
}

function render() {
  renderPanel("projects", [state.projects, state.active], renderProjects);
  // volatile per-poll fields (session elapsed_min, fractional today_minutes) are quantized
  // OUT of the render signature: the 1 s tickClock drives the clock, so the header must
  // not rebuild on every 1.5 s poll while a session is running
  renderPanel("header", [state.projects, state.active, state.tasks, state.status && { ...state.status, session: state.status.session ? { ...state.status.session, elapsed_min: 0 } : null, today_minutes: state.status.today_minutes == null ? null : Math.round(state.status.today_minutes) }], renderHeader);
  renderPanel("kanban", [state.tasks, state.features, state.timeByTask,
                          state.session ? state.session.task_id : null], renderKanban);
  renderPanel("tagfilter", [state.allTags, state.filterTag], renderTagFilter);
  renderPanel("session", [state.session, state.tasks], renderSessionPanel);
  renderPanel("sessions", state.recentSessions, renderSessions);
  renderPanel("logs", [state.logs, state.logQuery], renderLogs);
  renderPanel("git", [state.gitStatus, state.gitCommits, state.gitUnparsed, state.gitTaskFilter,
                      state.gitBranches, state.gitChanges, state.gitChangesOpen, state.gitSelFile,
                      state.gitSelFiles, state.gitError, state.tasks], renderGit);
  renderPanel("features", [state.features, state.tasks], renderFeatures);
  renderPanel("chat", [state.chat, state.ai, state.aiBusy], renderChat);
  renderPanel("stats", [state.stats, state.statsFilters, state.estimates], renderStats);
  renderPanel("timeline", [state.timeline, state.tlFilter], renderTimeline);
  renderPanel("ui", [state.tab, state.filterTag, state.filterPrio, state.taskQuery, state.logQuery,
                     state.gitTaskFilter, state.gitUnparsed, state.statsFilters, state.subtaskView,
                     state.collapsed, state.tlFilter, state.sidebarCollapsed], saveUi);
  // show the reset-filters button only when a filter is actually active
  const anyFilter = !!(state.filterTag || state.filterPrio || state.taskQuery);
  $("#btn-reset-filters").classList.toggle("hidden", !anyFilter);
  updateTabDots();
  updateTitle(); // project switch renames the window immediately (tickClock also refreshes it)
}

// project colors end up in a style attribute: only accept #rrggbb (the API
// validates on write too — this is defense in depth against a hand-edited DB)
function cssColor(c) {
  return /^#[0-9a-fA-F]{6}$/.test(c || "") ? c : "#8a8f98";
}

// per-engine accent colors, shown as a small dot next to the engine label
const ENGINE_COLORS = { unity: "#38bdf8", unreal: "#e8e8ec", godot: "#7aa2f7", web: "#a3e635", other: "#9aa3b2" };
function engineColor(e) { return ENGINE_COLORS[e] || ENGINE_COLORS.other; }

// comma-separated input -> normalized language list (same rules as the core)
function parseLangs(raw) {
  return String(raw || "").split(",").map((s) => s.trim().toLowerCase().replace(/\s+/g, "-"))
    .filter(Boolean).slice(0, 12);
}

// the ACTIVE project's color themes the chrome (header line, active tab, selection)
function applyProjectTheme(p) {
  if (p) document.documentElement.style.setProperty("--proj", cssColor(p.color));
  else document.documentElement.style.removeProperty("--proj");
}

function renderProjects() {
  const ul = $("#project-list");
  ul.innerHTML = "";
  applyProjectTheme(state.projects.find((x) => x.slug === state.active));
  for (const p of state.projects.filter((x) => !x.archived)) {  // archived hidden from the sidebar
    const li = document.createElement("li");
    if (p.slug === state.active) li.classList.add("active");
    li.innerHTML =
      `<span class="dot" style="background:${cssColor(p.color)}"></span>` +
      `<span>${escapeHtml(p.name)}</span>` +
      `<span class="meta"><span class="dot eng" style="background:${engineColor(p.engine)}"></span>${escapeHtml(T("engine." + p.engine))}</span>` +
      (p.path ? `<button class="proj-open" title="${T("gui.proj.open_folder_tt")}">📂</button>` : "") +
      `<button class="proj-edit" title="${T("gui.proj.edit_tt")}">✎</button>`;
    li.title = p.slug + " — " + T("pstatus." + p.status) +
      (p.languages && p.languages.length ? " — " + p.languages.join(", ") : "") +
      (p.path ? " — 📂 " + p.path : "");
    li.onclick = () => runAction(async () => {
      await api.post("/api/projects/set-active", { ref: p.slug });
      notify(T("gui.toast.project_active", { name: p.name }));
      await loadAll();
    });
    // open-folder button must not trigger set-active
    const openBtn = li.querySelector(".proj-open");
    if (openBtn) openBtn.onclick = (e) => {
      e.stopPropagation();
      runAction(() => openProjectFolder(p.slug));
    };
    // edit button must not trigger set-active
    li.querySelector(".proj-edit").onclick = (e) => {
      e.stopPropagation();
      openEditModal(p);
    };
    ul.appendChild(li);
  }
}

function renderHeader() {
  const h = $("#project-header-left");
  const p = state.projects.find((x) => x.slug === state.active);
  if (!p) {
    h.innerHTML = `<h1>${T("gui.header.no_project")}</h1><span class='sub'>${T("gui.header.no_project_sub")}</span>`;
    return;
  }
  const open = state.tasks.filter((t) => t.status === "todo" || t.status === "in_progress").length;
  const st = state.status || {};
  const prog = st.progress ? ` · ${st.progress.pct_weighted}%` : "";
  const today = st.today_minutes != null ? ` · ⏱ ${Math.round(st.today_minutes)} ${T("gui.chart.minutes").toLowerCase().slice(0, 3)}.` : "";
  const streak = st.streak_days != null ? ` · 🔥 ${st.streak_days} ${T("gui.day_unit")}` : "";
  const langs = (p.languages || []).map((l) => `<span class="lang-badge">${escapeHtml(l)}</span>`).join("");
  h.innerHTML =
    `<span class="dot" style="background:${cssColor(p.color)}"></span>` +
    `<h1 id="header-proj" class="clickable" title="${T("gui.proj.edit_tt")}">${escapeHtml(p.name)}</h1>` +
    (p.path ? `<button id="btn-open-proj-folder" class="btn btn-sm" title="${T("gui.proj.open_folder_tt")}">${T("gui.proj.open_folder")}</button>` : "") +
    `<span class="sub"><span class="dot eng" style="background:${engineColor(p.engine)}"></span>` +
    `${T("gui.header.sub", { engine: escapeHtml(T("engine." + p.engine)), status: T("pstatus." + p.status), open })}${prog}${today}${streak}${langs}</span>`;
  const hp = $("#header-proj");
  if (hp) hp.onclick = () => openEditModal(p); // click the project name in the header to edit it
  const openFolderBtn = $("#btn-open-proj-folder");
  if (openFolderBtn) openFolderBtn.onclick = () => runAction(() => openProjectFolder(p.slug));
  // header progress bar (themed by the project color) + dynamic tab title
  const pct = st.progress ? st.progress.pct_weighted : 0;
  $("#header-progress-bar").style.width = pct + "%";
  $("#header-progress").title = Math.round(pct) + "% — " + p.name;
  if (!state.session) document.title = p.name + " · DevDash";
  const sel = $("#session-task");
  const inProgress = state.tasks.filter((t) => t.status === "in_progress");
  const current = sel.value;
  sel.innerHTML = `<option value="">${T("gui.no_task")}</option>` +
    inProgress.map((t) => `<option value="${t.id}">#${t.id} ${escapeHtml(t.title)}</option>`).join("");
  if (inProgress.some((t) => String(t.id) === current)) sel.value = current;
}

const COL_CAP_DEFAULT = 40; // cards rendered per column before the "voir plus" button
const COL_CAP_STEP = 100;   // extra cards revealed per click on "voir plus"

/* Corbeille (soft-deleted tasks): restore, or delete forever (2-step arm).
   state.tasks already holds ONLY the trash rows while state.showTrash is on. */
function renderTrashView() {
  const ul = $("#trash-list");
  if (!ul) return;
  ul.innerHTML = "";
  const empty = $("#trash-empty"), head = $("#trash-head");
  if (!state.showTrash) { // the normal path sets #tasks-empty itself (line below)
    if (head) head.classList.add("hidden");
    return;
  }
  if (!state.tasks.length) { // proper empty state, not a bare dim line
    if (empty) empty.classList.remove("hidden");
    if (head) head.classList.add("hidden");
    return;
  }
  if (empty) empty.classList.add("hidden");
  if (head) {
    head.classList.remove("hidden");
    const c = $("#trash-count");
    if (c) c.textContent = state.tasks.length;
  }
  for (const t of state.tasks) {
    const li = document.createElement("li");
    li.className = "trash-row card " + t.status;
    li.innerHTML =
      `<span class="t-text">#${t.id} ${escapeHtml(t.title)}</span>` +
      `<span class="prio prio-${t.priority}">${T("prio." + t.priority)}</span>` +
      `<span class="spacer"></span>` +
      `<button class="btn btn-sm" data-act="restore" title="${T("gui.trash.restore_tt")}">${T("gui.trash.restore")}</button>` +
      `<button class="btn btn-sm btn-danger" data-act="perm" title="${T("gui.trash.perm_tt")}">${T("gui.trash.perm")}</button>`;
    li.querySelector('[data-act="restore"]').onclick = () => runAction(async () => {
      await api.post("/api/tasks/restore", { id: t.id });
      notify(T("gui.trash.restored"));
      await loadAll();
    });
    const perm = li.querySelector('[data-act="perm"]');
    perm.onclick = () => {
      if (perm.dataset.armed === "1") {
        runAction(async () => {
          await api.post("/api/tasks/delete", { id: t.id, permanent: true });
          notify(T("gui.trash.perm_done"));
          await loadAll();
        });
      } else {
        armOnce(perm, { confirmKey: "gui.trash.perm_confirm", resetKey: "gui.trash.perm", ms: ARM_LONG_MS });
      }
    };
    ul.appendChild(li);
  }
}

const seenTaskIds = new Set(); // ids already rendered once -> new cards get the "land" pop
const justDoneIds = new Set(); // ids just marked done -> their card flashes green once in the done column

function renderKanban() {
  // switching project = fresh board: don't pop every card, only genuinely new ones
  if (renderKanban._pid !== state.active) {  // state.active is the project SLUG
    renderKanban._pid = state.active;
    seenTaskIds.clear();
  }
  // corbeille view replaces the columns (the columns stay in the DOM, hidden)
  $("#kanban").classList.toggle("hidden", !!state.showTrash);
  $("#trash-view").classList.toggle("hidden", !state.showTrash);
  // trash is a focused view: hide the recent-sessions block underneath it
  $("#sessions-section").classList.toggle("hidden", !!state.showTrash);
  // keep the trash toolbar button in sync with the view (active state + aria)
  const tb = $("#btn-trash");
  if (tb) {
    tb.classList.toggle("btn-active", !!state.showTrash);
    tb.setAttribute("aria-pressed", String(!!state.showTrash));
  }
  if (state.showTrash) { $("#tasks-empty").classList.add("hidden"); renderTrashView(); return; }
  renderTrashView();
  // total minutes per task, for the card time badge (filled by loadTaskMinutes)
  const timeByTask = state.timeByTask || {};
  const statuses = ["todo", "in_progress", "done", "cancelled"]; // cancelled get their own column (reopenable)
  for (const status of statuses) {
    const col = $("#col-" + status);
    if (!col) continue;
    const colEl = col.closest(".col");
    // stacked view (Notion-style): only root tasks get a column card; their
    // subtasks live inside the parent card's expandable stack
    const list = state.tasks.filter((t) => t.status === status &&
      (state.subtaskView === "flat" || t.parent_id == null));
    $("#count-" + status).textContent = list.length;
    // collapsed column (click on the header): only the header + count stay visible
    if (colEl) {
      colEl.classList.toggle("col-collapsed", !!state.collapsed[status]);
      const h3 = colEl.querySelector("h3");
      if (h3) h3.title = T(state.collapsed[status] ? "gui.col.expand_tt" : "gui.col.collapse_tt");
    }
    if (state.collapsed[status]) { col.innerHTML = ""; continue; }
    col.innerHTML = "";
    // cap the number of rendered cards (long projects): "voir plus" raises it
    const cap = state.colCaps[status] || COL_CAP_DEFAULT;
    const shown = list.slice(0, cap);
    for (const t of shown) {
      const card = taskCard(t);
      if (seenTaskIds.size && !seenTaskIds.has(t.id)) card.classList.add("landed"); // freshly created: pop in
      seenTaskIds.add(t.id);
      if (justDoneIds.has(t.id)) { card.classList.add("done-flash"); justDoneIds.delete(t.id); } // animate once
      col.appendChild(card);
    }
    if (list.length > shown.length) {
      const more = document.createElement("button");
      more.className = "btn btn-sm col-more";
      more.textContent = T("gui.task.more", { n: list.length - shown.length });
      more.onclick = (e) => {
        e.stopPropagation();
        state.colCaps[status] = (state.colCaps[status] || COL_CAP_DEFAULT) + COL_CAP_STEP;
        renderKanban();
      };
      col.appendChild(more);
    }
  }
  // tab badge: number of open tasks
  const openCount = state.tasks.filter((t) => t.status === "todo" || t.status === "in_progress").length;
  const badge = $("#badge-tasks");
  badge.textContent = openCount;
  badge.title = T("gui.badge.open", { n: openCount });
  badge.classList.toggle("hidden", openCount === 0);
  // empty state only when no card is rendered at all
  const rendered = statuses.reduce((n, st) => n + ($("#col-" + st) ? $("#col-" + st).children.length : 0), 0);
  $("#tasks-empty").classList.toggle("hidden", rendered > 0);
}

// all descendant ids of a task in the currently loaded flat list (cycle-safe)
function descendantIdsOf(id) {
  const out = new Set(), q = [id];
  while (q.length) {
    const cur = q.pop();
    for (const x of state.tasks) {
      if (x.parent_id === cur && !out.has(x.id)) { out.add(x.id); q.push(x.id); }
    }
  }
  return out;
}

function taskCard(t) {
  const card = document.createElement("div");
  const running = !!(state.session && state.session.task_id === t.id); // session active on this task
  card.className = "card " + t.status + (t.parent_id != null ? " is-subtask" : "") + (running ? " running" : "");
  card.draggable = true; // drag & drop between kanban columns (or onto a card = subtask)
  card.dataset.taskId = t.id;
  card.tabIndex = 0; // keyboard: Enter/Space opens the edit modal
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `#${t.id} ${t.title}`);
  card.addEventListener("keydown", (e) => {
    // typing inside the card (subtask input, button…) must NOT open the edit modal
    if (e.target.closest("input, textarea, select, button")) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openTaskModal(t).catch(() => {}); }
  });
  const tags = t.tags.map((x) => `<span class="tag">${escapeHtml(x)}</span>`).join("");
  const est = t.estimate_min != null ? `<span class="est">${T("gui.est", { n: t.estimate_min })}</span>` : ""; // 0 min is a real estimate
  const mins = Math.round((state.timeByTask || {})[t.id] || 0);
  // estimate vs actual on one chip: ⏱ 132m/90m, tinted when off the estimate
  let timeBadge = "";
  if (t.estimate_min != null && mins > 0) {
    const over = t.estimate_min > 0 && mins > t.estimate_min * EST_OVER_RATIO;
    const under = t.estimate_min > 0 && mins < t.estimate_min * EST_UNDER_RATIO;
    timeBadge = `<span class="est${over ? " over" : under ? " under" : ""}" title="${T("gui.est_vs_tt", { act: mins, est: t.estimate_min })}">⏱ ${mins}m/${t.estimate_min}m</span>`;
  } else if (mins > 0) {
    timeBadge = `<span class="est" title="${T("gui.task.time_badge")}">⏱ ${mins}m</span>`;
  }
  const subs = state.tasks.filter((x) => x.parent_id === t.id);
  const subsDone = subs.filter((x) => x.status === "done").length;
  const pct = subs.length ? Math.round(100 * subsDone / subs.length) : 0;
  const subBadge = subs.length
    ? `<span class="tag dim" title="${T("gui.task.subtasks", { done: subsDone, total: subs.length })}">☑ ${subsDone}/${subs.length}</span>` : "";
  const parentRef = t.parent_id ? `<span class="tag dim">${T("gui.task.parent_ref", { id: t.parent_id })}</span>` : "";
  const feat = t.feature_id ? `<span class="tag dim">⚑ #${t.feature_id}</span>` : "";
  const expanded = !!state.expandedParents[t.id];
  // Notion-style: a parent card carries a progress bar + an expandable stack.
  // The stack renders whenever the card is EXPANDED (even with zero subtasks),
  // so the ＋ sous-tâche button always has its inline input to focus.
  const chevron = (subs.length || expanded)
    ? `<button class="mini-btn sub-toggle" data-act="toggle-subs" title="${T(expanded ? "gui.task.collapse" : "gui.task.expand")}">${expanded ? "▾" : "▸"}</button>` : "";
  const progressBar = subs.length
    ? `<div class="sub-progress" title="${pct}%"><div class="bar" style="width:${pct}%"></div></div>` : "";
  const actBtn = (act, label, title) =>
    `<button class="btn btn-sm" data-act="${act}"${title ? ` title="${title}"` : ""}>${label}</button>`;
  const actions = [];
  if (t.status === "todo") actions.push(actBtn("in_progress", T("gui.task.start")));
  if (t.status === "in_progress") {
    actions.push(actBtn("done", T("gui.task.done")));
    actions.push(actBtn("todo", T("gui.task.todo")));
  }
  if (t.status === "done" || t.status === "cancelled") actions.push(actBtn("todo", T("gui.task.reopen")));
  if (t.status !== "cancelled" && t.status !== "done") actions.push(actBtn("cancelled", T("gui.task.cancel")));
  // quick "add subtask" affordance on every non-done card (Trello-style stacking)
  if (t.status !== "done" && t.status !== "cancelled")
    actions.push(actBtn("add-sub", `＋${T("gui.task.add_sub")}`, T("gui.task.add_sub_tt")));
  card.innerHTML =
    `<div class="title">${chevron}<span class="t-text">#${t.id} ${escapeHtml(t.title)}</span></div>` +
    progressBar +
    `<div class="row"><span class="prio prio-${t.priority}">${T("prio." + t.priority)}</span>${tags}${est}${timeBadge}${subBadge}${parentRef}${feat}</div>` +
    (expanded ? `<div class="sub-stack" data-stack="${t.id}"></div>` : "") +
    `<div class="actions">${actions.join("")}</div>`;
  // render the subtask stack (recursive mini-cards) once the container exists
  const stack = card.querySelector(".sub-stack");
  if (stack) {
    for (const s of subs) stack.appendChild(subCard(s, 1));
    // inline "new subtask" row
    const row = document.createElement("div");
    row.className = "sub-add-row";
    row.innerHTML = `<span class="dim">↳</span><input type="text" class="sub-add-input" data-i18n-ph="gui.task.add_sub_ph" placeholder="${T("gui.task.add_sub_ph")}">`;
    const input = row.querySelector("input");
    input.addEventListener("keydown", (e) => {
      e.stopPropagation(); // no key from this input may reach the card handler
      if (e.key !== "Enter") return;
      const title = input.value.trim();
      if (!title) return;
      runAction(async () => {
        const r = await api.post("/api/tasks/add", { title, parent_id: t.id, priority: t.priority });
        notify(T("gui.toast.sub_added", { id: r.id, parent: t.id }));
        await loadAll();
      });
    });
    input.addEventListener("click", (e) => e.stopPropagation());
    stack.appendChild(row);
    // all subtasks done -> Notion-style hint to finish the parent
    if (subs.length > 0 && subsDone === subs.length && t.status !== "done") {
      const hint = document.createElement("button");
      hint.className = "btn btn-sm btn-primary sub-all-done";
      hint.textContent = T("gui.task.all_subs_done", { id: t.id });
      hint.onclick = (e) => { e.stopPropagation(); runAction(async () => {
        await completeTask(t.id);
        await loadAll();
      }); };
      stack.appendChild(hint);
    }
  }
  card.querySelectorAll("button[data-act]").forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); runAction(async () => {
      if (b.dataset.act === "toggle-subs") {
        state.expandedParents[t.id] = !state.expandedParents[t.id];
        renderKanban();
        return;
      }
      if (b.dataset.act === "add-sub") {
        state.expandedParents[t.id] = true;
        renderKanban(); // re-render replaces the card node: query the fresh input
        const fresh = document.querySelector(`[data-stack="${t.id}"] .sub-add-input`);
        if (fresh) fresh.focus();
        return;
      }
      await api.post("/api/tasks/status", { id: t.id, status: b.dataset.act });
      if (b.dataset.act === "done") justDoneIds.add(t.id); // green flash on landing
      const toastKey = { done: "gui.toast.task_done", in_progress: "gui.toast.task_started",
        todo: "gui.toast.task_reopened", cancelled: "gui.toast.task_cancelled" }[b.dataset.act];
      if (toastKey) notify(T(toastKey, { id: t.id }));
      await loadAll();
    }); };
  });
  // click on the card (not on a button) opens the edit modal
  card.onclick = () => openTaskModal(t).catch(() => {});
  // NOTE: dropping a card ON another card (=> subtask) is handled by DELEGATED
  // listeners on the static .cards zones (wireSubtaskDnD), not per-card: the
  // zones are never re-rendered, so the handlers survive renderKanban() and
  // e.target.closest(".card") always finds the card under the cursor.
  card.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", String(t.id));
    document.body.dataset.dragTaskId = String(t.id);
    e.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
    document.body.classList.add("dnd-active");
    // custom ghost: a lifted, slightly rotated clone follows the cursor
    // (the default drag image is a flat snapshot of the source card)
    const ghost = card.cloneNode(true);
    ghost.classList.remove("dragging");
    ghost.classList.add("drag-ghost");
    ghost.style.width = card.offsetWidth + "px";
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, ghost.offsetWidth / 2, 24);
    requestAnimationFrame(() => ghost.remove()); // the image is snapshotted synchronously
  });
  card.addEventListener("dragend", () => {
    card.classList.remove("dragging");
    document.body.classList.remove("dnd-active");
    delete document.body.dataset.dragTaskId;
  });
  return card;
}

// one subtask inside a parent card's stack: compact row, click opens the modal,
// ✓ quick-completes. Renders its own children indented (recursive, depth-capped).
function subCard(t, depth) {
  const el = document.createElement("div");
  el.className = "sub-card st-" + t.status + (depth > 2 ? " sub-deep" : "");
  el.style.marginLeft = (depth > 1 ? 14 : 0) + "px";
  el.dataset.taskId = t.id;
  const icon = { todo: "○", in_progress: "▶", done: "✔", cancelled: "✖" }[t.status] || "○";
  const kids = state.tasks.filter((x) => x.parent_id === t.id);
  const kidsDone = kids.filter((x) => x.status === "done").length;
  const kidBadge = kids.length ? ` <span class="dim">☑${kidsDone}/${kids.length}</span>` : "";
  el.innerHTML = `<span class="sub-icon">${icon}</span> <span class="sub-title">#${t.id} ${escapeHtml(t.title)}</span>${kidBadge}`;
  el.title = `#${t.id} ${t.title}`;
  el.onclick = (e) => { e.stopPropagation(); openTaskModal(t).catch(() => {}); };
  if (t.status !== "done" && t.status !== "cancelled") {
    const ok = document.createElement("button");
    ok.className = "mini-btn sub-done";
    ok.textContent = "✔";
    ok.title = T("gui.task.done");
    ok.onclick = (e) => { e.stopPropagation(); runAction(async () => {
      await completeTask(t.id);
      await loadAll();
    }); };
    el.appendChild(ok);
  }
  // nested children (grand-subtasks) shown as deeper rows when the parent card
  // is expanded — depth capped to keep the card readable
  if (kids.length && depth < 3 && state.expandedParents[t.id]) {
    for (const k of kids) el.appendChild(subCard(k, depth + 1));
  }
  return el;
}

/* ---------- kanban drag & drop: drop a card on a column to change status ---------- */

function wireKanbanDnD() {
  document.querySelectorAll("#kanban .col").forEach((col) => {
    const target = col.querySelector(".cards");
    // enter/leave counter: dragleave fires when entering a child card too,
    // which made the hover highlight flicker
    col.addEventListener("dragenter", (e) => { e.preventDefault(); col._dragDepth = (col._dragDepth || 0) + 1; col.classList.add("drop-hover"); });
    col.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      // auto-scroll the column when the cursor nears its top/bottom edge
      const r = target.getBoundingClientRect();
      const EDGE = 40, STEP = 30;
      if (e.clientY < r.top + EDGE) target.scrollBy({ top: -STEP, behavior: "smooth" });
      else if (e.clientY > r.bottom - EDGE) target.scrollBy({ top: STEP, behavior: "smooth" });
    });
    col.addEventListener("dragleave", () => { col._dragDepth = Math.max(0, (col._dragDepth || 0) - 1); if (!col._dragDepth) col.classList.remove("drop-hover"); });
    col.addEventListener("drop", (e) => {
      e.preventDefault();
      col._dragDepth = 0;
      col.classList.remove("drop-hover");
      const id = e.dataTransfer.getData("text/plain");
      const status = col.dataset.status;
      if (!id || !status) return;
      const task = state.tasks.find((t) => String(t.id) === id);
      if (task && task.status === status) return; // no-op drop: no event, no reload
      runAction(async () => {
        await api.post("/api/tasks/status", { id: parseInt(id, 10), status });
        // small visual feedback: flash the target column, pop the landed card
        col.classList.add("drop-flash");
        setTimeout(() => col.classList.remove("drop-flash"), 460);
        await loadAll();
        const landed = col.querySelector(`[data-task-id="${id}"]`);
        if (landed) landed.classList.add("landed");
      });
    });
  });
}

/* ---------- drag & drop ONTO a card: stack the dragged task as a subtask ----------
 * Delegated on the static .cards zones (they are never re-rendered, unlike the
 * cards themselves), so the handlers keep working after every renderKanban().
 * The column handlers sit on .col (a PARENT of the zone), so stopping
 * propagation here prevents the drop from also changing the card's status. */
function wireSubtaskDnD() {
  document.querySelectorAll("#kanban .cards").forEach((zone) => {
    zone.addEventListener("dragover", (e) => {
      const card = e.target.closest ? e.target.closest(".card") : null;
      if (!card) return;
      const dragId = Number(document.body.dataset.dragTaskId || 0);
      const target = state.tasks.find((x) => x.id === Number(card.dataset.taskId));
      if (!target || !dragId || dragId === target.id || descendantIdsOf(dragId).has(target.id)) return;
      e.preventDefault();          // allow the drop on the card
      e.stopPropagation();         // the column must not treat this as a status drop
      e.dataTransfer.dropEffect = "move";
      document.querySelectorAll(".card.drop-sub").forEach((c) => c.classList.remove("drop-sub"));
      card.classList.add("drop-sub");
    });
    zone.addEventListener("dragleave", (e) => {
      if (!e.relatedTarget || !zone.contains(e.relatedTarget)) {
        document.querySelectorAll(".card.drop-sub").forEach((c) => c.classList.remove("drop-sub"));
      }
    });
    zone.addEventListener("drop", (e) => {
      const card = e.target.closest ? e.target.closest(".card") : null;
      if (!card) return; // gap between cards: let the column do the status drop
      const dragId = Number(document.body.dataset.dragTaskId || e.dataTransfer.getData("text/plain") || 0);
      const target = state.tasks.find((x) => x.id === Number(card.dataset.taskId));
      document.querySelectorAll(".card.drop-sub").forEach((c) => c.classList.remove("drop-sub"));
      if (!target || !dragId || dragId === target.id || descendantIdsOf(dragId).has(target.id)) return;
      e.preventDefault();
      e.stopPropagation();
      runAction(async () => {
        await api.post("/api/tasks/update", { id: dragId, parent_id: target.id });
        state.expandedParents[target.id] = true;
        notify(T("gui.toast.task_parented", { id: dragId, parent: target.id }));
        await loadAll();
      });
    });
  });
}

/* ---------- focus management for modals/palette: remember the opener, trap Tab,
 * restore focus on close (WCAG 2.4.3 / 2.1.2) ---------- */
let _lastFocus = null;
function rememberFocus() { _lastFocus = document.activeElement; }
function restoreFocus() {
  if (_lastFocus && document.contains(_lastFocus) && _lastFocus.focus) _lastFocus.focus();
  _lastFocus = null;
}
const MODAL_IDS = ["#palette", "#modal", "#modal-edit", "#modal-task", "#modal-feat", "#modal-help"];
function openModalEl() { return MODAL_IDS.find((id) => !$(id).classList.contains("hidden")) || null; }

// animated close: fade the overlay out, THEN hide (instant hide would cut the animation)
function hideAnimated(el) {
  if (!el || el.classList.contains("hidden")) return;
  el.classList.add("closing");
  const done = () => { el.classList.add("hidden"); el.classList.remove("closing"); };
  el.addEventListener("animationend", done, { once: true });
  setTimeout(done, 200); // fallback when animations are off (prefers-reduced-motion)
}
// Tab / Shift+Tab must stay inside the open modal (focus trap). Returns true if consumed.
function modalTrapTab(e) {
  const el = openModalEl();
  if (!el || e.key !== "Tab") return false;
  const focusables = $(el).querySelectorAll(
    "button, input, select, textarea, [tabindex]:not([tabindex='-1'])");
  if (!focusables.length) { e.preventDefault(); return true; }
  const first = focusables[0], last = focusables[focusables.length - 1];
  const active = document.activeElement;
  if (e.shiftKey && (active === first || !$(el).contains(active))) { last.focus(); e.preventDefault(); return true; }
  if (!e.shiftKey && (active === last || !$(el).contains(active))) { first.focus(); e.preventDefault(); return true; }
  return false;
}

/* ---------- task edit modal ---------- */

async function openTaskModal(t) {
  rememberFocus();
  state.editingTaskId = t.id;
  // the list payload truncates long descriptions: fetch the full row so saving
  // the modal can never write the truncated text back over the full one
  const full = await api.get("/api/tasks/show?id=" + t.id).catch(() => null);
  if (full && full.task) t = full.task;
  // set only the text span: textContent on the h3 would destroy the copy button
  $("#task-modal-title-text").textContent = T("gui.task.edit_title", { id: t.id });
  $("#t-title").value = t.title;
  $("#t-desc").value = t.description || "";
  $("#t-priority").value = t.priority;
  $("#t-tags").value = (t.tags || []).join(", ");
  $("#t-est").value = t.estimate_min == null ? "" : String(t.estimate_min); // 0 is a real estimate, keep it
  // parent select: all tasks of the project except this one and its descendants
  // (the core rejects cycles too, but hiding them avoids a confusing 400)
  const desc = descendantIdsOf(t.id);
  const others = state.tasks.filter((x) => x.id !== t.id && !desc.has(x.id));
  $("#t-parent").innerHTML = `<option value="">${T("gui.task.none")}</option>` +
    others.map((x) => `<option value="${x.id}">#${x.id} ${escapeHtml(x.title)}</option>`).join("");
  $("#t-parent").value = t.parent_id || "";
  $("#t-feature").innerHTML = `<option value="">${T("gui.task.none")}</option>` +
    (state.features || []).map((f) => `<option value="${f.id}">⚑ ${escapeHtml(f.name)}</option>`).join("");
  $("#t-feature").value = t.feature_id || "";
  // reset the delete button to its un-armed state
  const delBtn = $("#btn-delete-task");
  delBtn.dataset.armed = "";
  delBtn.textContent = T("gui.task.delete");
  delBtn.classList.remove("btn-danger-armed");
  // reset the AI verdict box; show it only when the AI (and the flag) is on
  const aiVerdictOn = state.ai.enabled && (state.ai.flags || {}).task_verdict !== false;
  $("#task-verdict-box").classList.toggle("hidden", !aiVerdictOn);
  $("#task-verdict-result").textContent = "";
  $("#btn-task-verify-approve").classList.add("hidden");
  state.taskVerdict = null;
  $("#modal-task").classList.remove("hidden");
  $("#t-title").focus();
  // total time logged on this task (sessions with task_id = this task)
  runAction(async () => {
    const r = await api.get("/api/sessions?task=" + t.id + "&limit=1000");
    const rows = r.sessions || [];
    const mins = Math.round(rows.reduce((a, s) => a + (s.duration_min || 0), 0));
    $("#task-time").textContent = rows.length ? T("gui.task.time", { min: mins, n: rows.length }) : "";
  });
}
function closeTaskModal() { hideAnimated($("#modal-task")); state.editingTaskId = null; restoreFocus(); }

async function saveTaskModal() {
  const id = state.editingTaskId;
  if (id == null) return;
  const title = $("#t-title").value.trim();
  if (!title) { showError(T("gui.err.title_required")); return; }
  const est = parseInt($("#t-est").value, 10);
  const parent = $("#t-parent").value;
  const feat = $("#t-feature").value;
  await api.post("/api/tasks/update", {
    id,
    title,
    description: $("#t-desc").value,
    priority: $("#t-priority").value,
    tags: $("#t-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
    estimate_min: Number.isNaN(est) ? null : est,
    parent_id: parent ? parseInt(parent, 10) : null,   // null clears the parent
    feature_id: feat ? parseInt(feat, 10) : null,       // null detaches the feature
  });
  closeTaskModal();
  await loadAll();
}

function renderTagFilter() {
  const sel = $("#filter-tag");
  const tags = [...new Set(state.allTags.length ? state.allTags : state.tasks.flatMap((t) => t.tags))].sort();
  sel.innerHTML = `<option value="">${T("gui.all_tags")}</option>` +
    tags.map((t) => `<option value="${escapeHtml(t)}" ${t === state.filterTag ? "selected" : ""}>${escapeHtml(t)}</option>`).join("");
}

/* ---------- session panel ---------- */

function fmtHms(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? String(h).padStart(2, "0") + ":" : "") +
    String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

function renderSessionPanel() {
  const running = !!state.session;
  $("#session-timer").classList.toggle("hidden", !running);
  $("#btn-session-start").classList.toggle("hidden", running);
  $("#btn-session-stop").classList.toggle("hidden", !running);
  $("#session-note").classList.toggle("hidden", !running);
  $("#session-task").classList.toggle("hidden", running);
  if (running) {
    $("#session-kind").textContent = T("kind." + state.session.kind) + (state.session.task_id ? ` · ${T("gui.task_ref", { id: state.session.task_id })}` : "");
    tickClock();
  }
}

// window/tab title: live timer while a session runs, else the active project name
function updateTitle() {
  const p = state.projects.find((x) => x.slug === state.active);
  let prefix = "";
  if (state.session) {
    const started = new Date(state.session.started_at).getTime();
    prefix = "⏱ " + fmtHms((Date.now() - started) / 1000) + " · ";
  }
  document.title = prefix + (p ? `DevDash · ${p.name}` : "DevDash");
}

function tickClock() {
  updateTitle();
  const lu = $("#last-update"); // "maj il y a X s" freshness hint, refreshed every second
  if (lu && state.lastLoadAt) {
    const s = Math.max(0, Math.round((Date.now() - state.lastLoadAt) / 1000));
    lu.textContent = T("gui.last_update", { s });
  }
  if (!state.session) return;
  const started = new Date(state.session.started_at).getTime();
  const txt = fmtHms((Date.now() - started) / 1000);
  $("#session-clock").textContent = txt;
  // planned end reached -> toast + desktop notification, once per session
  if (state.session.planned_min) {
    const end = started + state.session.planned_min * 60000;
    if (Date.now() >= end && state.sessionNotifyId !== state.session.id) {
      state.sessionNotifyId = state.session.id;
      notify(T("gui.sess.done"));
      if (window.Notification && Notification.permission === "granted")
        new Notification("DevDash", { body: T("gui.sess.done") });
    }
  }
}

function renderSessions() {
  const ul = $("#session-list");
  ul.innerHTML = "";
  for (const s of state.recentSessions || []) {
    const li = document.createElement("li");
    const dur = s.duration_min != null ? `${s.duration_min} ${T("gui.chart.minutes").toLowerCase()}` : T("gui.session.running");
    li.innerHTML =
      `<span class="mono dim">${timeAgo(s.started_at)}</span> · ` +
      `<span>${T("kind." + s.kind)}</span> · <b>${dur}</b>` +
      (s.task_id ? ` · <span class="dim">#${s.task_id}</span>` : "") +
      (s.note ? ` — <span class="dim">${escapeHtml(s.note)}</span>` : "");
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = `<li class="dim">${T("gui.sessions.empty")}</li>`;
}

/* ---------- devlog ---------- */

function renderLogs() {
  const ul = $("#log-list");
  ul.innerHTML = "";
  for (const e of state.logs) {
    const li = document.createElement("li");
    li.className = "log-entry";
    const tags = e.tags.map((x) => `<span class="tag">${escapeHtml(x)}</span>`).join("");
    li.innerHTML =
      `<div class="log-head"><span class="mono dim">${timeAgo(e.created_at)}</span>` +
      `${tags}${e.task_id ? ` <span class="tag">#${e.task_id}</span>` : ""}` +
      (state.logTrash
        ? `<span class="log-tools"><button class="mini-btn log-restore" title="${T("gui.trash.restore_tt")}">↩ ${T("gui.trash.restore")}</button>` +
          `<button class="mini-btn log-perm" title="${T("gui.trash.perm_tt")}">${T("gui.trash.perm")}</button></span>`
        : `<span class="log-tools"><button class="mini-btn log-edit" title="${T("gui.log.edit_tt")}">✎</button>` +
          `<button class="mini-btn log-del" title="${T("gui.log.del_tt")}">🗑</button></span>`) +
      `</div>` +
      `<div class="log-content">${escapeHtml(e.content)}</div>`;
    if (state.logTrash) {
      // corbeille: restore, or delete forever (2-step arm)
      li.querySelector(".log-restore").onclick = () => runAction(async () => {
        await api.post("/api/logs/restore", { id: e.id });
        notify(T("gui.trash.restored"));
        await loadAll();
      });
      const perm = li.querySelector(".log-perm");
      perm.onclick = () => {
        if (perm.dataset.armed === "1") {
          runAction(async () => {
            await api.post("/api/logs/delete", { id: e.id, permanent: true });
            notify(T("gui.trash.perm_done"));
            await loadAll();
          });
        } else {
          armOnce(perm, { confirmKey: "gui.trash.perm_confirm", resetKey: "gui.trash.perm", ms: ARM_LONG_MS });
        }
      };
      ul.appendChild(li);
      continue;
    }
    // inline edit: swap the content div for an input, save on Enter/blur
    li.querySelector(".log-edit").onclick = async () => {
      // list rows truncate long content: fetch the full entry before editing,
      // so saving can never write the truncated text back over the full one
      const full = await api.get("/api/logs/show?id=" + e.id).catch(() => null);
      const content = (full && full.entry && full.entry.content) || e.content;
      const div = li.querySelector(".log-content");
      const input = document.createElement("input");
      input.type = "text";
      input.className = "log-edit-input";
      input.value = content;
      div.replaceWith(input);
      input.focus();
      let cancelled = false; // Escape cancels instead of saving via the induced blur
      const save = () => runAction(async () => {
        if (cancelled || !input.isConnected) return; // Escape removed it / already saved
        const v = input.value.trim();
        if (v && v !== content) await api.post("/api/logs/update", { id: e.id, content: v });
        await loadAll();
      });
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") save();
        if (ev.key === "Escape") { cancelled = true; renderLogs(); } // discard, no POST
      });
      input.addEventListener("blur", save);
    };
    // two-step delete
    const delBtn = li.querySelector(".log-del");
    delBtn.onclick = () => {
      if (delBtn.dataset.armed === "1") {
        runAction(async () => { await api.post("/api/logs/delete", { id: e.id }); await loadAll(); });
      } else {
        armOnce(delBtn, { confirmKey: "⚠", resetKey: "🗑", titleKey: "gui.log.del_confirm" });
      }
    };
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = `<li class="dim">${T(state.logTrash ? "gui.trash.empty" : "gui.log.empty")}</li>`;
}

async function addLog() {
  const content = $("#log-content").value.trim();
  if (!content) return;
  const tags = $("#log-tags").value.split(",").map((s) => s.trim()).filter(Boolean);
  await api.post("/api/logs/add", { content, tags });
  $("#log-content").value = "";
  $("#log-tags").value = "";
  notify(T("gui.toast.log_added"));
  await loadAll();
}

/* ---------- git ---------- */

function renderGit() {
  const st = state.gitStatus || {};
  // task filter select (all tasks of the active project)
  const sel = $("#git-task-filter");
  const cur = state.gitTaskFilter;
  sel.innerHTML = `<option value="">${T("gui.git.all_tasks")}</option>` +
    state.tasks.map((t) => `<option value="${t.id}">#${t.id} ${escapeHtml(t.title)}</option>`).join("");
  if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  const box = $("#git-status");
  if (!st.linked) {
    // distinguish "no repo linked" (normal) from a real load error
    box.textContent = state.gitError ? ("⚠ " + state.gitError) : T("gui.git.none");
  } else if (st.error) {
    // linked repo but git errored (e.g. .git removed after linking): show it,
    // never render "undefined" placeholders
    box.textContent = "⚠ " + st.error;
  } else {
    const last = st.last_commit;
    box.innerHTML =
      `${T("gui.git.repo")} <span class="mono">${escapeHtml(st.repo_path || "")}</span> · ${T("gui.git.branch")} <b>${escapeHtml(st.branch || "?")}</b> · ` +
      `${T("gui.git.commits_today", { n: st.commits_today ?? 0 })} · ${T("gui.git.cache", { n: st.cached_commits ?? 0 })}` +
      (last ? `<br>${T("gui.git.last", { short: escapeHtml(last.short), msg: escapeHtml(last.message) })}` : "");
  }
  // branches select (read-only overview; picking one shows its recent commits
  // in the terminal output below — no checkout, ever)
  const bsel = $("#git-branches");
  const br = state.gitBranches;
  if (br && br.branches && br.branches.length) {
    bsel.classList.remove("hidden");
    const cur = bsel.value || br.current || "";
    bsel.innerHTML = br.branches.map((b) =>
      `<option value="${escapeHtml(b.name)}" ${b.current ? "selected" : ""}>${b.current ? "⑂ " : ""}${escapeHtml(b.name)}</option>`).join("");
    if ([...bsel.options].some((o) => o.value === cur)) bsel.value = cur;
    else if (br.current) bsel.value = br.current;
  } else {
    bsel.classList.add("hidden");
    bsel.innerHTML = "";
  }
  // changed-files panel (GitHub Desktop "Changes")
  renderGitChanges();
  const ul = $("#git-commit-list");
  ul.innerHTML = "";
  for (const c of state.gitCommits || []) {
    const li = document.createElement("li");
    const link = c.parsed_task_id
      ? `<span class="tag">#${c.parsed_task_id}</span>`
      : c.parsed_feature
        ? `<span class="tag">feat:${escapeHtml(c.parsed_feature)}</span>`
        : `<span class="tag dim">${T("gui.git.unrecognized")}</span>`;
    li.innerHTML =
      `<span class="mono commit-hash" data-hash="${escapeHtml(c.hash)}" title="${T("gui.copy_hash")}">${escapeHtml(c.short)}</span> ` +
      `<span class="dim">${timeAgo(c.committed_at)}</span> ` +
      `<span class="dim">${escapeHtml(c.author)}</span> — ${escapeHtml(c.message)} ${link}`;
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = `<li class="dim">${T("gui.git.empty")}</li>`;
  // click a short hash to copy the full one
  ul.querySelectorAll(".commit-hash").forEach((el) => {
    el.onclick = () => {
      const h = el.dataset.hash || "";
      if (navigator.clipboard && navigator.clipboard.writeText)
        navigator.clipboard.writeText(h).then(() => notify(T("gui.copied")), () => notify(T("gui.copied")));
      else notify(T("gui.copied"));
    };
  });
}

// GitHub-Desktop-style "Changes" panel: one row per changed file, click = diff
function renderGitChanges() {
  const panel = $("#git-changes");
  const list = $("#git-changes-list");
  const count = $("#git-changes-count");
  const ch = state.gitChanges;
  panel.classList.toggle("hidden", !state.gitChangesOpen);
  if (!ch || !ch.files) {
    if (count) count.textContent = "";
    list.innerHTML = "";
    updateGitSelUI([]);
    return;
  }
  if (count) count.textContent = ch.count ? String(ch.count) : "";
  $("#git-changes-stats").textContent = ch.count
    ? `+${ch.insertions} −${ch.deletions}`
    : T("gui.git.changes_none");
  renderGitLinkedTask(ch);
  list.innerHTML = "";
  for (const f of ch.files) {
    const li = document.createElement("li");
    if (state.gitSelFile === f.path) li.classList.add("sel");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!state.gitSelFiles[f.path];
    cb.title = T("gui.git.select_tt");
    cb.onclick = (e) => {
      e.stopPropagation();
      if (cb.checked) state.gitSelFiles[f.path] = true;
      else delete state.gitSelFiles[f.path];
      updateGitSelUI(ch.files);
    };
    const badge = `<span class="st-badge st-${f.kind}">${f.staged && f.x !== "?" ? f.x : (f.y !== " " ? f.y : f.x)}</span>`;
    const stat = (f.added || f.deleted)
      ? `<span class="diffstat"><span class="add">+${f.added}</span> <span class="del">−${f.deleted}</span></span>` : "";
    li.innerHTML = `${badge}<span class="fname">${escapeHtml(f.path)}</span>${stat}`;
    li.insertBefore(cb, li.firstChild);
    li.title = T("gui.git.view_diff_tt");
    li.onclick = () => runAction(() => gitShowDiff(f.path, f.staged && f.x !== "?"));
    list.appendChild(li);
  }
  if (!list.children.length) list.innerHTML = `<li class="dim">${T("gui.git.changes_none")}</li>`;
  updateGitSelUI(ch.files);
}

// changes-panel header: "n selected" + select-all checkbox state
function updateGitSelUI(files) {
  const paths = (files || []).map((f) => f.path);
  const n = paths.filter((p) => state.gitSelFiles[p]).length;
  const el = $("#git-sel-count");
  if (el) el.textContent = n ? T("gui.git.sel_count", { n }) : "";
  const all = $("#git-sel-all");
  if (all) all.checked = paths.length > 0 && n === paths.length;
}

// Banner: uncommitted changes after the last commit + a linked task => offer to
// affirm completion (two-click confirm) or to verify with the AI first.
function renderGitLinkedTask(ch) {
  const box = $("#git-linked-task");
  const lt = ch && ch.linked_task;
  if (!lt || !ch.count || lt.status === "done" || lt.status === "cancelled") {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  const src = lt.source === "session"
    ? T("gui.git.linked_session")
    : T("gui.git.linked_commit", { hash: lt.commit || "" });
  box.innerHTML = "";
  const label = document.createElement("span");
  label.innerHTML = `🧩 ${T("gui.git.linked", { id: lt.id, title: escapeHtml(lt.title) })} <span class="dim">(${escapeHtml(src)})</span>`;
  box.appendChild(label);
  // AI verify (only when the assistant + the task_verdict flag are on)
  if (state.ai.enabled && (state.ai.flags || {}).task_verdict !== false) {
    const vb = document.createElement("button");
    vb.className = "btn btn-sm";
    vb.textContent = "🤖 " + T("gui.task.verify");
    const vr = document.createElement("span");
    vr.className = "dim small";
    vb.onclick = () => runAction(async () => {
      vb.disabled = true;
      vr.textContent = T("gui.git.running");
      try {
        const r = await api.post("/api/ai/task_verdict", { id: lt.id });
        const v = r.verdict || {};  // API shape: {verdict: {seems_complete, reasoning, ...}, task, applied}
        vr.textContent = (v.seems_complete ? "✔ " : "✖ ") + (v.reasoning || "");
        vr.classList.toggle("ok", !!v.seems_complete);
        if (v.seems_complete) approveBtn.classList.remove("hidden");
      } catch (e) {
        vr.textContent = String(e.message || e);
      } finally {
        vb.disabled = false;
      }
    });
    box.appendChild(vb);
    box.appendChild(vr);
  }
  // two-click armed "mark done" — a human always confirms the completion
  const confirmDone = () => runAction(async () => {
    await completeTask(lt.id);
    await loadAll();
  });
  const doneBtn = document.createElement("button");
  doneBtn.className = "btn btn-sm btn-primary";
  doneBtn.textContent = T("gui.git.mark_done", { id: lt.id });
  doneBtn.onclick = (e) => {
    e.stopPropagation();
    if (!armOnce(doneBtn, { confirmKey: "gui.git.mark_done_confirm", resetKey: "gui.git.mark_done", ms: ARM_SHORT_MS, cls: "btn-danger-armed", titleParams: { id: lt.id } })) {
      confirmDone();
    }
  };
  box.appendChild(doneBtn);
  // shown only after a positive AI verdict: explicit human approve
  const approveBtn = document.createElement("button");
  approveBtn.className = "btn btn-sm btn-primary hidden";
  approveBtn.textContent = T("gui.task.verify_approve");
  approveBtn.onclick = (e) => { e.stopPropagation(); confirmDone(); };
  box.appendChild(approveBtn);
}

// one shared error renderer for the git terminal pane
function renderGitError(err, st) {
  const out = $("#git-cmd-out");
  out.classList.remove("hidden");
  out.textContent = String(err.message || err);
  out.classList.add("err");
  if (st) st.textContent = "error";
}

// run a read-only git command and render its output in the git terminal pane
async function gitShowDiffCmd(cmd) {
  const out = $("#git-cmd-out");
  const st = $("#git-cmd-status");
  out.classList.remove("hidden", "err");
  out.textContent = T("gui.git.running");
  st.textContent = "";
  try {
    await gitRenderOut(await api.post("/api/git/run", { cmd }), cmd);
  } catch (err) {
    renderGitError(err);
  }
}

// show the diff of one changed file in the git terminal output (read-only)
async function gitShowDiff(path, staged) {
  state.gitSelFile = path;
  renderGitChanges();
  const quoted = '"' + String(path).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  await gitShowDiffCmd((staged ? "diff --cached -- " : "diff -- ") + quoted);
}

// render a {ok, stdout, stderr, cmd} git result in the terminal pane
async function gitRenderOut(r, cmd) {
  const out = $("#git-cmd-out");
  out.classList.remove("hidden", "err");
  let text = "$ git " + (r.cmd || cmd) + "\n" + (r.stdout || "") +
    (r.stderr ? (r.stdout && !r.stdout.endsWith("\n") ? "\n" : "") + r.stderr : "");
  out.textContent = text || "(no output)\n";
  out.classList.toggle("err", !r.ok);
}

// remote READ on the linked repo: fetch / pull --ff-only / remote -v
// (remote writes are refused server-side by the allowlist — project rule)
async function gitRemote(cmd) {
  const st = $("#git-cmd-status");
  st.textContent = "";
  try {
    const r = await api.post("/api/git/remote", { cmd });
    await gitRenderOut(r, cmd);
    st.textContent = r.ok ? "ok" : "refused";
  } catch (err) {
    renderGitError(err, st);
  }
}

// combined read-only diff of the checked files (working tree or index)
async function gitDiffSelection() {
  const files = Object.keys(state.gitSelFiles).filter((p) => state.gitSelFiles[p]);
  if (!files.length) { notify(T("gui.git.no_sel")); return; }
  const staged = $("#git-diff-staged").checked;
  const st = $("#git-cmd-status");
  const btn = $("#btn-git-diff-sel");
  st.textContent = "";
  setBtnBusy(btn, true, T("gui.git.running"));
  try {
    const r = await api.post("/api/git/diff", { files, staged });
    await gitRenderOut(r, (staged ? "diff --cached -- " : "diff -- ") + files.join(" "));
    st.textContent = r.ok ? "ok" : "refused";
    popStatus(st);
  } catch (err) {
    renderGitError(err, st);
    popStatus(st);
  } finally {
    setBtnBusy(btn, false);
  }
}


// open the project's connected folder in the OS file explorer
async function openProjectFolder(ref) {
  try {
    const r = await api.post("/api/projects/open", { ref });
    notify(T("gui.toast.folder_opened", { path: r.opened }));
  } catch (e) {
    showError(e.message);
  }
}

async function gitOpenRepo() {
  try {
    const r = await api.post("/api/git/open", {});
    notify(T("gui.toast.repo_opened", { path: r.opened }));
  } catch (e) {
    showError(e.message);
  }
}

async function gitRefreshChanges() {
  try {
    state.gitChanges = await api.post("/api/git/changes", {});
  } catch { /* keep the old list */ }
  renderGitChanges();
}

async function gitLink() {
  const path = $("#git-path").value.trim();
  if (!path) return;
  await api.post("/api/git/link", { path });
  await api.post("/api/git/sync", {});
  await loadAll();
}

async function gitSync() {
  const r = await api.post("/api/git/sync", {});
  // sum across ALL linked repos, not just the first one
  const n = (r.repos || []).reduce((sum, repo) => sum + (repo.new || 0), 0);
  notify(T("gui.toast.git_synced", { n }));
  await loadAll();
}

async function gitIgnore() {
  const st = $("#git-ignore-status");
  st.textContent = "";
  const r = await api.post("/api/git/ignore", {});
  st.textContent = r.updated
    ? T("gui.git.ignore_done", { n: r.lines })
    : T("gui.git.ignore_already");
}

async function gitRun() {
  const cmd = $("#git-cmd").value.trim();
  if (!cmd) return;
  const st = $("#git-cmd-status");
  const btn = $("#btn-git-run");
  st.textContent = "";
  setBtnBusy(btn, true, T("gui.git.running"));
  try {
    const out = $("#git-cmd-out");
    out.classList.remove("hidden");
    await gitShowDiffCmd(cmd);
    // after gitShowDiffCmd, reflect ok/refused in the status line
    st.textContent = out.classList.contains("err") ? "refused (read-only mode)" : "ok";
    popStatus(st);
  } finally {
    setBtnBusy(btn, false);
  }
}

/* ---------- features ---------- */

async function renderFeatures() {
  const ul = $("#feature-list");
  ul.innerHTML = "";
  for (const f of state.features || []) {
    const li = document.createElement("li");
    li.className = "feature-card status-" + f.status;
    const p = f.progress || { total: 0, done: 0, pct: 0 };
    const bar = p.total ? Math.round(p.pct) : 0;
    if (state.featTrash) {
      // corbeille: restore, or delete forever (2-step arm)
      li.className = "feature-card trash-row";
      li.innerHTML =
        `<div class="feat-head"><b class="feat-name">⚑ ${escapeHtml(f.name)}</b> ` +
        `<span class="tag">${T("fstatus." + f.status)}</span>` +
        `<span class="log-tools"><button class="mini-btn feat-restore" title="${T("gui.trash.restore_tt")}">↩ ${T("gui.trash.restore")}</button>` +
        `<button class="mini-btn feat-perm" title="${T("gui.trash.perm_tt")}">${T("gui.trash.perm")}</button></span></div>`;
      li.querySelector(".feat-restore").onclick = () => runAction(async () => {
        await api.post("/api/features/restore", { ref: f.name });
        notify(T("gui.trash.restored"));
        await loadAll();
      });
      const perm = li.querySelector(".feat-perm");
      perm.onclick = () => {
        if (perm.dataset.armed === "1") {
          runAction(async () => {
            await api.post("/api/features/delete", { ref: f.name, permanent: true });
            notify(T("gui.trash.perm_done"));
            await loadAll();
          });
        } else {
          armOnce(perm, { confirmKey: "gui.trash.perm_confirm", resetKey: "gui.trash.perm", ms: ARM_LONG_MS });
        }
      };
      ul.appendChild(li);
      continue;
    }
    let actions = "";
    const aiVerdict = state.ai.enabled && (state.ai.flags || {}).feature_verdict && f.status === "open";
    if (f.status === "open") {
      actions = `<button class="btn btn-sm" data-act="done">${T("gui.feat.done")}</button>` +
                `<select data-act="attach"><option value="">${T("gui.feat.link_task")}</option>` +
                state.tasks.map((t) => `<option value="${t.id}">#${t.id} ${escapeHtml(t.title)}</option>`).join("") +
                `</select>` +
                (aiVerdict ? `<button class="btn btn-sm" data-act="verdict">${T("gui.feat.verdict")}</button>` : "");
    } else if (f.status === "proposed") {
      actions = `<button class="btn btn-sm btn-primary" data-act="approve">${T("gui.feat.approve")}</button>` +
                `<button class="btn btn-sm" data-act="reject">${T("gui.feat.reject")}</button>` +
                (f.reasoning ? `<div class="dim feat-reasoning">${escapeHtml(f.reasoning)}</div>` : "");
    } else if (f.status === "done") {
      actions = `<button class="btn btn-sm" data-act="reopen">${T("gui.feat.reopen")}</button>`;
    }
    li.innerHTML =
      `<div class="feat-head"><b class="feat-name" data-open="${escapeHtml(f.name)}">⚑ ${escapeHtml(f.name)}</b> ` +
      `<span class="tag">${T("fstatus." + f.status)}</span> ` +
      (f.source === "ai" && f.confidence != null ? `<span class="dim">${T("gui.feat.confidence", { pct: (f.confidence * 100).toFixed(0) })}</span>` : "") +
      `<span class="log-tools"><button class="mini-btn feat-edit" title="${T("gui.feat.edit_tt")}">✎</button>` +
      `<button class="mini-btn feat-del" title="${T("gui.feat.del_tt")}">🗑</button></span>` +
      `</div>` +
      `<div class="feat-progress"><div class="bar" style="width:${bar}%"></div>` +
      `<span class="dim">${T("gui.feat.progress", { done: p.done, total: p.total, pct: p.pct })}</span></div>` +
      `<div class="feat-actions">${actions}</div>` +
      `<div class="feat-detail hidden" id="feat-detail-${f.id}"></div>`;
    // click the feature name to expand its linked tasks (with detach buttons)
    li.querySelector(".feat-name").onclick = () => runAction(async () => {
      state.featOpen = state.featOpen === f.name ? null : f.name;
      await loadFeatures();
      renderFeatures();
    });
    if (state.featOpen === f.name) {
      const det = li.querySelector(".feat-detail");
      det.classList.remove("hidden");
      loadFeatDetail(f, det); // async: fills when ready, never blocks the other cards
    }
    li.querySelectorAll("button[data-act]").forEach((b) => {
      b.onclick = () => runAction(async () => {
        const act = b.dataset.act;
        if (act === "done") await api.post("/api/features/done", { ref: f.name });
        if (act === "approve") await api.post("/api/features/approve", { ref: f.name });
        if (act === "reject") await api.post("/api/features/reject", { ref: f.name, feedback: T("gui.feat.reject_feedback") });
        if (act === "reopen") await api.post("/api/features/reopen", { ref: f.name });
        if (act === "verdict") {
          setAiBusy();
          try {
            const r = await api.post("/api/ai/verdict", { ref: f.name });
            state.chat.push({ role: "assistant", time: nowTime(),
              content: T("gui.verdict.chat", { name: f.name,
                verdict: r.verdict.seems_complete ? T("verdict.complete") : T("verdict.incomplete"),
                pct: (r.verdict.confidence * 100).toFixed(0) }) + `\n${r.verdict.reasoning}` +
                (r.proposed ? T("gui.verdict.proposed") : "") });
          } catch (e) {
            state.chat.push({ role: "assistant", error: true, content: e.message, time: nowTime() });
          } finally {
            clearAiBusy();
          }
          renderChat();
        }
        await loadAll();
      });
    });
    const sel = li.querySelector("select[data-act=attach]");
    if (sel) sel.onchange = () => runAction(async () => {
      if (sel.value) {
        await api.post("/api/features/attach", { ref: f.name, task_id: parseInt(sel.value, 10) });
        await loadAll();
      }
    });
    const editBtn = li.querySelector(".feat-edit");
    if (editBtn) editBtn.onclick = () => openFeatModal(f);
    const delBtn = li.querySelector(".feat-del");
    if (delBtn) delBtn.onclick = (e) => {
      e.stopPropagation();
      if (delBtn.dataset.armed === "1") {
        runAction(async () => {
          await api.post("/api/features/delete", { ref: f.name });
          notify(T("gui.toast.feat_deleted", { name: f.name }));
          await loadAll();
        });
      } else {
        armOnce(delBtn, { confirmKey: "?", resetKey: "🗑" });
      }
    };
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = `<li class="dim">${T(state.featTrash ? "gui.trash.empty" : "gui.feat.empty")}</li>`;
  // tab badge: features proposed by the AI awaiting a human decision
  const proposed = (state.features || []).filter((f) => f.status === "proposed").length;
  const bf = $("#badge-features");
  bf.textContent = proposed;
  bf.title = T("gui.badge.proposed", { n: proposed });
  bf.classList.toggle("hidden", proposed === 0);
}

async function addFeature() {
  const name = $("#feature-name").value.trim();
  if (!name) { showError(T("gui.err.name_required")); return; }
  await api.post("/api/features/add", { name });
  $("#feature-name").value = "";
  await loadAll();
}

// fetch + render one expanded feature's linked tasks (called without await on purpose)
async function loadFeatDetail(f, det) {
  try {
    const d = await api.get("/api/features/show?ref=" + encodeURIComponent(f.name));
    const tasks = (d.progress && d.progress.tasks) || [];
    det.innerHTML = tasks.length
      ? tasks.map((t) =>
          `<div class="feat-task">#${t.id} ${escapeHtml(t.title)} ` +
          `<span class="tag">${T("gui.col." + t.status)}</span>` +
          (f.status === "open" ? `<button class="mini-btn feat-detach" data-id="${t.id}" title="${T("gui.feat.detach_tt")}">✕</button>` : "") +
          `</div>`).join("")
      : `<div class="dim">—</div>`;
    det.querySelectorAll(".feat-detach").forEach((b) => {
      b.onclick = () => runAction(async () => {
        await api.post("/api/features/detach", { ref: f.name, task_id: parseInt(b.dataset.id, 10) });
        await loadAll();
      });
    });
  } catch { det.innerHTML = ""; }
}

/* ---------- feature edit modal ---------- */

function openFeatModal(f) {
  rememberFocus();
  state.editingFeatId = f.id;
  $("#feat-modal-title").textContent = T("gui.feat.edit_title", { id: f.id });
  $("#ft-name").value = f.name;
  $("#ft-desc").value = f.description || "";
  const delBtn = $("#btn-feat-delete");
  delBtn.dataset.armed = "";
  delBtn.textContent = T("gui.edit.delete");
  delBtn.classList.remove("btn-danger-armed");
  $("#modal-feat").classList.remove("hidden");
  $("#ft-name").focus();
}
function closeFeatModal() { hideAnimated($("#modal-feat")); state.editingFeatId = null; restoreFocus(); }

async function saveFeatModal() {
  const id = state.editingFeatId;
  if (id == null) return;
  const name = $("#ft-name").value.trim();
  if (!name) { showError(T("gui.err.name_required")); return; }
  const f = (state.features || []).find((x) => x.id === id);
  await api.post("/api/features/update", { ref: f ? f.name : String(id), name, description: $("#ft-desc").value });
  notify(T("gui.toast.feat_updated"));
  closeFeatModal();
  await loadAll();
}

$("#btn-cancel-feat").onclick = closeFeatModal;
$("#btn-save-feat").onclick = () => runAction(saveFeatModal);
$("#btn-feat-delete").onclick = () => {
  const id = state.editingFeatId;
  if (id == null) return;
  const btn = $("#btn-feat-delete");
  const f = (state.features || []).find((x) => x.id === id);
  if (armOnce(btn, { confirmKey: "gui.feat.delete_confirm", resetKey: "gui.edit.delete", ms: ARM_LONG_MS, cls: "btn-danger-armed" })) return;
  runAction(async () => {
    await api.post("/api/features/delete", { ref: f ? f.name : String(id) });
    notify(T("gui.toast.feat_deleted", { name: f ? f.name : id }));
    closeFeatModal();
    await loadAll();
  });
};

/* ---------- stats & charts ---------- */

const charts = {};
const CHART_COLORS = ["#2563eb", "#3fb27f", "#d9a13b", "#e5534b", "#38bdf8", "#f472b6", "#a3e635", "#fb923c"];

function chartDefaults() {
  if (window.Chart) {
    const cs = getComputedStyle(document.documentElement); // theme-aware (dark/light)
    Chart.defaults.color = cs.getPropertyValue("--text-dim").trim() || "#9aa3b2";
    Chart.defaults.borderColor = cs.getPropertyValue("--border").trim() || "#262c38";
    Chart.defaults.font.family = "Segoe UI, system-ui, sans-serif";
  }
}

function makeBar(canvasId, labels, data, label, color) {
  if (!window.Chart) return;
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: { labels, datasets: [{ label, data,
      backgroundColor: color, borderRadius: 4 }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true } } },
  });
}

function makeDoughnut(canvasId, labels, data) {
  if (!window.Chart) return;
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(document.getElementById(canvasId), {
    type: "doughnut",
    data: { labels, datasets: [{ data, backgroundColor: CHART_COLORS }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: "right", labels: { boxWidth: 12 } } } },
  });
}

/* ---------- timeline tab (activity feed over the event bus) ---------- */

function fmtMin(m) {
  m = Math.round(m || 0);
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60), r = m % 60;
  return r ? h + "h" + String(r).padStart(2, "0") : h + "h";
}

function tlLabel(e) {
  const entity = e.entity || "event";
  const action = e.action || "";
  if (action.startsWith("status:")) return T("tstatus." + action.slice(7));
  const key = "tl." + entity + "." + action;
  if (I18N.messages && I18N.messages[key]) return T(key);
  const base = "tl." + entity;
  return (I18N.messages && I18N.messages[base]) ? T(base) : entity;
}

function tlDetail(e) {
  const pl = e.payload || {};
  for (const k of ["title", "message", "content", "slug", "name", "repo_path", "note"]) {
    if (typeof pl[k] === "string" && pl[k].trim()) {
      return pl[k].trim().replace(/\n/g, " ").slice(0, EVENT_LABEL_SNIPPET);
    }
  }
  if (e.entity === "git" && e.action === "commit") return String(pl.short || e.entity_id || "").slice(0, EVENT_LABEL_SNIPPET);
  return "";
}

function renderTimeline() {
  const ul = $("#tl-list");
  if (!ul) return;
  const rows = state.timeline || [];
  if (!rows.length) {
    ul.innerHTML = `<li class="tl-empty">${escapeHtml(T("gui.tl.empty"))}</li>`;
    return;
  }
  ul.innerHTML = rows.map((e) => {
    const d = tlDetail(e);
    return `<li class="tl-row"><span class="tl-icon">${e.icon || "•"}</span>` +
      `<span class="tl-label">${escapeHtml(tlLabel(e))}</span>` +
      `<span class="tl-detail"${d ? ` title="${escapeHtml(d)}"` : ""}>${escapeHtml(d)}</span>` +
      `<span class="tl-time">${timeAgo(e.created_at)}</span></li>`;
  }).join("");
}

/* ---------- estimate vs actual (stats tab box) ---------- */

function renderEstimates() {
  const box = $("#est-box");
  if (!box) return;
  const e = state.estimates;
  if (!e || !e.agg || !e.agg.with_estimate) {
    $("#est-summary").textContent = T("gui.est.none");
    $("#est-over-line").textContent = "";
    $("#est-tbody").innerHTML = "";
    return;
  }
  const a = e.agg;
  const pct = a.pct_of_estimate != null ? a.pct_of_estimate + "%" : "—";
  $("#est-summary").textContent = T("gui.est.summary", {
    n: a.with_estimate, est: fmtMin(a.est_total_min),
    act: fmtMin(a.actual_on_estimated_min), pct,
  });
  $("#est-over-line").textContent = T("gui.est.over_line", { over: a.over, under: a.under });
  const rows = (e.tasks || []).filter((r) => r.estimate_min != null).slice(0, ESTIMATES_TOP);
  const maxM = Math.max(1, ...rows.map((r) => Math.max(r.estimate_min, r.actual_min)));
  $("#est-tbody").innerHTML = rows.map((r) => {
    const over = r.estimate_min > 0 && r.actual_min > r.estimate_min * EST_OVER_RATIO;
    const estW = Math.round(100 * r.estimate_min / maxM);
    const actW = Math.round(100 * r.actual_min / maxM);
    return `<tr><td title="#${r.id} ${escapeHtml(r.title)}">#${r.id} ${escapeHtml(r.title.slice(0, EST_TITLE_SNIPPET))}</td>` +
      `<td class="num"><span class="est-bar-row"><span class="est-fill" style="width:${estW}%"></span></span> ${r.estimate_min}m</td>` +
      `<td class="num"><span class="est-bar-row${over ? " over" : ""}"><span class="act-fill" style="width:${actW}%"></span></span> ${Math.round(r.actual_min)}m</td></tr>`;
  }).join("");
}

async function downloadReport() {
  const body = { period: "week", ai: false };
  if (state.statsFilters.project) body.project = state.statsFilters.project;
  const r = await api.post("/api/report", body);
  const blob = new Blob([r.markdown], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `devdash-report-${r.project}-${r.start}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  notify(T("gui.toast.report_dl"));
}

function renderStats() {
  const s = state.stats;
  if (!s) return;
  if (state.tab !== "stats") return; // rebuilding charts on every poll is wasteful when hidden
  // rebuild the 5 charts only when the data changed; round the live "today"
  // minutes so a running session doesn't recreate all charts every 1.5 s poll
  const sig = JSON.stringify({ ...s, estimates: state.estimates, minutes: s.minutes && {
    today: Math.round(s.minutes.today), week: s.minutes.week,
    month: s.minutes.month, all: s.minutes.all } });
  if (sig === renderStats._sig) return;
  renderStats._sig = sig;
  chartDefaults();
  const groupLabel = T("gui.stats.group." + (s.group || "day"));
  const days = s.days || 14;
  // grouped labels: day -> MM-DD, week -> YYYY-Www, month -> YYYY-MM
  const lbl = (d) => ((s.group === "day") ? String(d).slice(5) : d); // called with d.date (a string)
  $("#stat-today").textContent = Math.round(s.minutes.today) + " min";
  $("#stat-week").textContent = Math.round(s.minutes.week) + " min";
  $("#stat-month").textContent = Math.round(s.minutes.month) + " min";
  $("#stat-streak").textContent = s.streak_days + " " + T("gui.day_unit");
  $("#stat-progress").textContent = (s.progress ? s.progress.pct_weighted : 0) + "%";
  $("#chart-time-title").textContent = T("gui.chart.time_title", { group: groupLabel, n: days });
  $("#chart-done-title").textContent = T("gui.chart.done_title", { group: groupLabel, n: days });
  $("#chart-projects-title").textContent = T("gui.chart.by_project", { n: days });
  makeBar("chart-time", s.time_by_day.map((d) => lbl(d.date)),
          s.time_by_day.map((d) => d.minutes), T("gui.chart.minutes"), "#2563eb");
  makeBar("chart-done", s.tasks_done_by_day.map((d) => lbl(d.date)),
          s.tasks_done_by_day.map((d) => d.count), T("gui.chart.tasks"), "#3fb27f");
  makeDoughnut("chart-tags", s.by_tag.map((t) => t.tag), s.by_tag.map((t) => t.tasks));
  const pct = s.progress ? s.progress.pct_weighted : 0;
  makeDoughnut("chart-progress", [T("gui.chart.done_label"), T("gui.chart.rest")], [pct, Math.max(0, 100 - pct)]);
  // per-project breakdown (colors come from each project)
  const bp = s.by_project || [];
  makeBar("chart-projects", bp.map((p) => p.name), bp.map((p) => p.minutes),
          T("gui.chart.minutes"), bp.map((p) => cssColor(p.color)));
  renderEstimates();
}

/* ---------- AI assistant ---------- */

const nowTime = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

const AI_BUSY_IDS = ["#btn-chat-send", "#chat-input", "#btn-chat-week", "#btn-chat-context", "#btn-plan", "#plan-input", "#btn-ai-suggest"];

/* While a provider call is in flight: typing bubble + live elapsed timer in the
   chat log, and the AI inputs are locked so nothing is sent twice. */
function setAiBusy(label) {
  state.aiBusy = { label: label || T("gui.ai.busy"), started: Date.now() };
  for (const id of AI_BUSY_IDS) { const el = $(id); if (el) el.disabled = true; }
  renderChat();
}
function clearAiBusy() {
  state.aiBusy = null;
  for (const id of AI_BUSY_IDS) { const el = $(id); if (el) el.disabled = false; }
  renderChat();
}
// live "Xs" counter on the typing bubble (cheap: a handful of nodes, once per second)
setInterval(() => {
  document.querySelectorAll(".chat-msg.typing").forEach((b) => {
    const el = b.querySelector(".elapsed");
    if (el && b.dataset.started) el.textContent = Math.floor((Date.now() - Number(b.dataset.started)) / 1000) + "s";
  });
}, 1000);

function chatEmptyState() {
  const wrap = document.createElement("div");
  wrap.className = "chat-empty";
  wrap.innerHTML = '<svg class="brand-mark chat-empty-mark" width="34" height="34" viewBox="0 0 20 20" aria-hidden="true"><rect width="20" height="20" rx="5" fill="#2563eb"/><path d="M3.5 11.5h3l2-5 3.5 8 2-4.5h3.5" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'; // static markup only
  const p = document.createElement("p");
  p.className = "dim";
  p.textContent = T("gui.chat.welcome");
  wrap.appendChild(p);
  const row = document.createElement("div");
  row.className = "chip-row";
  const chip = (label, fn) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = label;
    b.onclick = () => runAction(fn);
    row.appendChild(b);
  };
  chip(T("gui.chat.suggest_status"), () => { $("#chat-input").value = T("gui.chat.suggest_status"); chatSend(); });
  chip(T("gui.chat.week_user"), chatWeek);
  chip(T("gui.chat.suggest_plan"), () => { switchTab("ai"); $("#plan-input").focus(); });
  wrap.appendChild(row);
  return wrap;
}

function renderChat() {
  const box = $("#chat-log");
  box.innerHTML = "";
  if (!state.chat.length && !state.aiBusy) box.appendChild(chatEmptyState());
  for (const m of state.chat) {
    const div = document.createElement("div");
    div.className = "chat-msg " + m.role + (m.error ? " error" : "");
    const body = document.createElement("div");
    body.className = "chat-body";
    body.textContent = m.content; // textContent: chat text is never injected as HTML
    div.appendChild(body);
    if (m.proposals) {
      // ticket proposals from the project analysis: checkboxes + a create button
      // that only fires on an explicit user click (consent), never automatically
      const list = document.createElement("div");
      list.className = "proposal-list";
      m.proposals.forEach((p, i) => {
        const row = document.createElement("label");
        row.className = "proposal-item";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !m.applied;
        cb.disabled = !!m.applied;
        cb.dataset.idx = String(i);
        const txt = document.createElement("span");
        txt.textContent = p.title
          + (p.estimate_min ? ` (~${p.estimate_min} min)` : "")
          + (p.priority && p.priority !== "medium" ? ` · ${p.priority}` : "")
          + (p.tags && p.tags.length ? ` · #${p.tags.join(" #")}` : "")
          + (p.reason ? ` — ${p.reason}` : "");
        row.append(cb, txt);
        list.appendChild(row);
      });
      div.appendChild(list);
      if (!m.applied) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-sm btn-primary proposal-apply";
        btn.textContent = T("gui.suggest.create_btn", { n: m.proposals.length });
        btn.onclick = () => runAction(() => applyProposalMsg(m, list));
        div.appendChild(btn);
      } else {
        const tag = document.createElement("div");
        tag.className = "dim small";
        tag.textContent = T("gui.suggest.applied_tag");
        div.appendChild(tag);
      }
    }
    const meta = document.createElement("div");
    meta.className = "chat-meta";
    meta.textContent = [m.role === "user" ? T("gui.chat.you") : T("gui.chat.ai"), m.time, m.meta].filter(Boolean).join(" · ");
    div.appendChild(meta);
    box.appendChild(div);
  }
  if (state.aiBusy) {
    const div = document.createElement("div");
    div.className = "chat-msg assistant typing";
    div.dataset.started = String(state.aiBusy.started);
    const lbl = document.createElement("span");
    lbl.textContent = state.aiBusy.label;
    const dots = document.createElement("span");
    dots.className = "dots";
    dots.innerHTML = "<i></i><i></i><i></i>"; // static markup only
    const el = document.createElement("span");
    el.className = "elapsed";
    el.textContent = "0s";
    div.append(lbl, dots, el);
    box.appendChild(div);
  }
  box.scrollTop = box.scrollHeight;
}

/* persisted AI chat history (server-side, per project): loaded when the AI
   tab opens; ask exchanges are stored by /api/ai/ask itself */
async function loadAiChat() {
  try {
    const r = await api.get("/api/ai/chat");
    state.chat = (r.messages || []).map((m) => ({
      role: m.role, content: m.content, meta: m.meta || "",
      time: (m.created_at || "").slice(11, 19),
    }));
  } catch { /* keep the in-memory chat on error */ }
}

async function clearAiChat() {
  const btn = $("#btn-chat-clear");
  if (armOnce(btn, { confirmKey: "gui.chat.clear_confirm", resetKey: "🗑" })) return;
  btn.textContent = "🗑";
  await api.post("/api/ai/chat/clear", {});
  state.chat = [];
  renderChat();
  notify(T("gui.chat.cleared"));
}

async function chatSend() {
  if (state.aiBusy) return; // a call is already in flight
  const input = $("#chat-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  state.chat.push({ role: "user", content: q, time: nowTime() });
  renderChat();
  setAiBusy();
  try {
    const r = await api.post("/api/ai/ask", { question: q });
    state.chat.push({ role: "assistant", content: r.answer,
      meta: [r.provider, r.model, r.cloud ? "cloud" : null].filter(Boolean).join(" / "), time: nowTime() });
  } catch (e) {
    state.chat.push({ role: "assistant", error: true, content: e.message, time: nowTime() });
  } finally {
    clearAiBusy();
  }
}

async function chatWeek() {
  if (state.aiBusy) return;
  state.chat.push({ role: "user", content: T("gui.chat.week_user"), time: nowTime() });
  renderChat();
  setAiBusy();
  try {
    const r = await api.post("/api/ai/summarize", {});
    state.chat.push({ role: "assistant", content: r.summary,
      meta: [r.provider, r.cloud ? "cloud" : null].filter(Boolean).join(" / "), time: nowTime() });
  } catch (e) {
    state.chat.push({ role: "assistant", error: true, content: e.message, time: nowTime() });
  } finally {
    clearAiBusy();
  }
}

async function chatContext() {
  // local-only preview: shows exactly what a provider would receive
  try {
    const r = await api.get("/api/ai/context");
    state.chat.push({ role: "assistant", time: nowTime(),
      content: `📋 ${T("ai.context.line", { chars: r.chars, max: r.max_chars, provider: r.provider || "-", cloud: r.cloud })}\n\n${r.context}` });
  } catch (e) {
    state.chat.push({ role: "assistant", error: true, content: e.message, time: nowTime() });
  }
  renderChat();
}

async function chatPlan(apply) {
  if (state.aiBusy) return;
  const input = $("#plan-input");
  const title = input.value.trim();
  if (!title) return;
  setAiBusy(T("gui.ai.busy_plan"));
  try {
    const r = await api.post("/api/ai/plan", { title, apply });
    const lines = r.proposal.map((s, i) => `${i + 1}. ${s.title}${s.estimate_min ? ` (~${s.estimate_min}min)` : ""}`).join("\n");
    state.chat.push({
      role: "assistant",
      time: nowTime(),
      content: (r.applied
        ? T("gui.plan.applied", { n: r.created_task_ids.length, ids: r.created_task_ids.map((i) => "#" + i).join(", ") })
        : T("gui.plan.proposed", { lines })),
    });
    if (!r.applied) {
      // remember the pending proposal for the confirm button, but never render
      // an internal marker into the chat log
      state.pendingPlan = title;
      $("#btn-plan").textContent = T("gui.plan_create"); // 2nd click = create
    } else {
      state.pendingPlan = null;
      $("#btn-plan").textContent = T("gui.plan_btn");
      input.value = "";
    }
  } catch (e) {
    state.chat.push({ role: "assistant", error: true, content: e.message, time: nowTime() });
  } finally {
    clearAiBusy();
  }
  renderChat();
  await loadAll();
}

/* Project analysis -> ticket proposals (read-only). Creation goes through
   applyProposalMsg, which the user triggers explicitly via the create button. */
async function aiSuggest() {
  if (state.aiBusy) return;
  setAiBusy(T("gui.ai.busy_suggest"));
  try {
    const r = await api.post("/api/ai/suggest", {});
    state.chat.push({ role: "assistant", time: nowTime(),
      content: r.proposals.length ? T("gui.suggest.proposed", { n: r.proposals.length }) : T("gui.suggest.none"),
      proposals: r.proposals, rejected: r.rejected,
      meta: [r.provider, r.cloud ? "cloud" : null].filter(Boolean).join(" / ") });
  } catch (e) {
    state.chat.push({ role: "assistant", error: true, content: e.message, time: nowTime() });
  } finally {
    clearAiBusy();
  }
}

async function applyProposalMsg(m, listEl) {
  if (state.aiBusy) return;
  const tasks = [];
  listEl.querySelectorAll(".proposal-item input:checked").forEach((cb) => {
    const p = m.proposals[Number(cb.dataset.idx)];
    if (p) tasks.push({ title: p.title, description: p.description, priority: p.priority,
                        estimate_min: p.estimate_min, tags: p.tags });
  });
  if (!tasks.length) return;
  setAiBusy(T("gui.ai.busy_create"));
  try {
    const r = await api.post("/api/ai/suggest/apply", { tasks }); // explicit consent: the click above
    m.applied = true;
    state.chat.push({ role: "assistant", time: nowTime(),
      content: T("gui.suggest.created", { n: r.count, ids: r.created.map((i) => "#" + i).join(", ") }) });
  } catch (e) {
    state.chat.push({ role: "assistant", error: true, content: e.message, time: nowTime() });
  } finally {
    clearAiBusy();
  }
  await loadAll();
}

/* ---------- error surfacing: a failed action must never look like "nothing happened" ---------- */

function showError(msg) {
  const t = $("#toast");
  t.textContent = "⚠ " + msg;
  t.classList.remove("hidden");
  t.classList.remove("ok");
  clearTimeout(showError._h);
  showError._h = setTimeout(() => {
    t.classList.add("out");
    setTimeout(() => { t.classList.add("hidden"); t.classList.remove("out"); }, 170);
  }, TOAST_MS);
}

// wrap every UI action: API errors (400/500/...) land in the toast
async function runAction(fn) {
  try { await fn(); } catch (e) { showError(e.message); }
}

// mark a task done + toast (single place for the repeated 3-liner)
async function completeTask(id) {
  await api.post("/api/tasks/status", { id, status: "done" });
  justDoneIds.add(id); // the card flashes green once when it lands in the done column
  notify(T("gui.toast.task_done", { id }));
}

/* ---------- actions ---------- */

async function addTask() {
  const title = $("#new-task-title").value.trim();
  if (!title) { showError(T("gui.err.title_required")); return; } // never fail silently
  const tags = $("#new-task-tags").value.split(",").map((s) => s.trim()).filter(Boolean);
  const est = parseInt($("#new-task-est").value, 10);
  const feat = $("#new-task-feature").value;
  await api.post("/api/tasks/add", {
    title,
    priority: $("#new-task-priority").value,
    tags,
    estimate_min: Number.isNaN(est) ? null : est,
    feature_id: feat ? parseInt(feat, 10) : null, // link the feature at creation
  });
  $("#new-task-title").value = "";
  $("#new-task-tags").value = "";
  $("#new-task-est").value = "";
  $("#new-task-feature").value = "";
  notify(T("gui.toast.task_added", { title }));
  await loadAll();
  $("#new-task-title").focus(); // rapid entry: stay in the field for the next task
}

async function startSession() {
  const taskId = $("#session-task").value;
  const kind = $("#session-kind-sel").value;
  let minutes = parseInt($("#session-minutes").value, 10);
  if ($("#session-pomodoro").checked && Number.isNaN(minutes) && kind === "focus") {
    // pomodoro: pre-fill the focus duration from settings (pause stays manual in the GUI)
    minutes = (state.settings && state.settings.general.pomodoro_focus_min) || 25;
  }
  if ($("#session-pomodoro").checked && window.Notification && Notification.permission === "default")
    Notification.requestPermission(); // for the end-of-session desktop notification
  await api.post("/api/session/start", {
    kind,
    task_id: taskId ? parseInt(taskId, 10) : null,
    minutes: Number.isNaN(minutes) ? null : minutes,
  });
  notify(T("gui.toast.session_started"));
  await loadAll();
}

async function stopSession() {
  const note = $("#session-note").value.trim();
  const s = await api.post("/api/session/stop", { note });
  $("#session-note").value = "";
  const mins = s && s.duration_min != null ? Math.round(s.duration_min) : 0;
  notify(T("gui.toast.session_stopped", { min: mins }));
  await loadAll();
}

/* ---------- modal (new project) ---------- */

function openModal() {
  rememberFocus();
  $("#modal").classList.remove("hidden");
  $("#f-path").value = "";
  $("#f-detect").textContent = "";
  $("#f-langs").value = "";
  $("#f-name").focus();
}
function closeModal() { hideAnimated($("#modal")); restoreFocus(); }

// live-detect an existing project folder: engine + git repo + suggested name
let detectTimer = null;
let detectSeq = 0; // out-of-order /api/detect responses must not overwrite a newer path's hint
async function detectFolder() {
  const path = $("#f-path").value.trim();
  const hint = $("#f-detect");
  if (!path) { hint.textContent = ""; return; }
  const seq = ++detectSeq;
  try {
    const d = await api.get("/api/detect?path=" + encodeURIComponent(path));
    if (seq !== detectSeq) return; // a newer keystroke already fired: stale response
    if (!d.is_dir) {
      hint.textContent = T("gui.modal.detect_none");
      return;
    }
    hint.textContent = T("gui.modal.detected", {
      engine: T("engine." + d.engine),
      git: d.git_root ? d.git_root : T("gui.modal.detect_git_none"),
    });
    if (!$("#f-name").value.trim() && d.name) $("#f-name").value = d.name;
    if (d.engine && [...$("#f-engine").options].some((o) => o.value === d.engine))
      $("#f-engine").value = d.engine;
  } catch {
    hint.textContent = T("gui.modal.detect_none");
  }
}

async function createProject() {
  const name = $("#f-name").value.trim();
  if (!name) { showError(T("gui.err.name_required")); return; }
  const body = {
    name,
    engine: $("#f-engine").value,
    color: $("#f-color").value,
    status: $("#f-status").value,
    description: $("#f-desc").value,
    languages: parseLangs($("#f-langs").value),
    set_active: true,
  };
  const path = $("#f-path").value.trim();
  // a folder path turns this into "connect an existing project"
  // (engine auto-detect + git auto-link happen server-side)
  if (path) {
    await api.post("/api/projects/connect", { ...body, path });
  } else {
    await api.post("/api/projects", body);
  }
  closeModal();
  await loadAll();
}

/* ---------- modal (edit project: rename / engine / color / status / delete) ---------- */

function openEditModal(p) {
  rememberFocus();
  state.editingSlug = p.slug;
  $("#e-name").value = p.name;
  $("#e-engine").value = p.engine;
  $("#e-color").value = cssColor(p.color);
  $("#e-langs").value = (p.languages || []).join(", ");
  $("#e-status").value = p.status;
  $("#e-desc").value = p.description || "";
  $("#e-path").value = p.path || "";
  $("#e-archived").checked = !!p.archived;
  const delBtn = $("#btn-edit-delete");
  delBtn.textContent = T("gui.edit.delete");
  delBtn.classList.remove("btn-danger-armed");
  delBtn.dataset.armed = "";
  $("#e-delete-warn").classList.add("hidden");
  $("#modal-edit").classList.remove("hidden");
  $("#e-name").focus();
}
function closeEditModal() { hideAnimated($("#modal-edit")); state.editingSlug = null; restoreFocus(); }

async function saveEditProject() {
  const ref = state.editingSlug;
  if (!ref) return;
  const name = $("#e-name").value.trim();
  if (!name) return;
  await api.post("/api/projects/update", {
    ref,
    name,
    engine: $("#e-engine").value,
    color: $("#e-color").value,
    languages: parseLangs($("#e-langs").value),
    status: $("#e-status").value,
    description: $("#e-desc").value,
    path: $("#e-path").value.trim(), // empty string clears the linked folder
    archived: $("#e-archived").checked,
  });
  closeEditModal();
  await loadAll();
}

async function deleteEditProject() {
  const ref = state.editingSlug;
  if (!ref) return;
  const delBtn = $("#btn-edit-delete");
  // two-step confirm (armOnce): first click arms, second click deletes
  if (armOnce(delBtn, {
    confirmKey: "gui.edit.delete_confirm", resetKey: "gui.edit.delete", ms: ARM_LONG_MS, cls: "btn-danger-armed",
    onArm: () => $("#e-delete-warn").classList.remove("hidden"),
    onReset: () => $("#e-delete-warn").classList.add("hidden"),
  })) return;
  await api.post("/api/projects/delete", { ref });
  closeEditModal();
  await loadAll();
}

/* ---------- tabs ---------- */

function switchTab(tab) {
  // a persisted "ai" tab is invalid when the AI is disabled: fall back to tasks
  if (tab === "ai" && !state.ai.enabled) tab = "tasks";
  state.tab = tab;
  saveUi();
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  for (const t of ["tasks", "log", "git", "features", "ai", "settings", "stats", "timeline"]) {
    const page = $("#tab-" + t);
    if (t === tab) {
      page.classList.remove("hidden");
      page.classList.remove("enter"); void page.offsetWidth; // restart the animation
      page.classList.add("enter");
    } else {
      page.classList.add("hidden");
      page.classList.remove("enter");
    }
  }
  if (tab === "stats") {
    // charts are only built while the tab is visible; the stats payload is
    // loaded lazily (skipped on polls while on other tabs) -> load on switch
    runAction(async () => { await Promise.all([loadStats(), loadEstimates()]); renderStats(); });
  }
  if (tab === "git") {
    // same lazy-loading for the git extras (branches/changes subprocesses)
    runAction(async () => { await loadGit(); renderGit(); });
    // visiting the tab clears its activity dot (baseline = newest commit now)
    const newest = state.gitCommits && state.gitCommits[0];
    if (newest) state.gitSeenHash = newest.hash;
  }
  if (tab === "timeline") {
    state.tlSeenId = maxTlId(); // visiting the tab clears its activity dot
  }
  if (tab === "ai") {
    // persisted chat history: load on tab switch (NOT on every poll, so
    // in-flight local messages are not clobbered mid-conversation)
    runAction(async () => { await loadAiChat(); renderChat(); });
  }
  if (tab === "settings") {
    // settings are loaded lazily (skipped on polls while on other tabs), so
    // switching TO this tab must trigger the load explicitly
    runAction(async () => {
      await Promise.all([loadAiConfig(), loadSettings(), loadUpdateStatus()]);
    });
  }
}

/* ---------- wiring ---------- */

$("#btn-add-task").onclick = () => runAction(addTask);
$("#new-task-title").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(addTask); });
$("#btn-new-project").onclick = openModal;
$("#f-path").addEventListener("input", () => {
  clearTimeout(detectTimer);
  detectTimer = setTimeout(() => runAction(detectFolder), 400); // debounce
});

/* native OS folder picker (server-side dialog): pre-fills the input, the user
   still confirms the link/connect explicitly */
async function browsePathInto(inputSel, titleKey) {
  const r = await api.post("/api/fs/pick", { title: T(titleKey) });
  if (r && r.path) {
    const el = $(inputSel);
    el.value = r.path;
    el.dispatchEvent(new Event("input", { bubbles: true })); // re-triggers the folder auto-detect debounce
  }
}
$("#btn-browse-fpath").onclick = () => runAction(() => browsePathInto("#f-path", "gui.pick_folder"));
$("#btn-browse-gitpath").onclick = () => runAction(() => browsePathInto("#git-path", "gui.pick_repo"));
$("#btn-cancel-modal").onclick = closeModal;
$("#btn-create-project").onclick = () => runAction(createProject);
$("#btn-cancel-edit").onclick = closeEditModal;
$("#btn-save-edit").onclick = () => runAction(saveEditProject);
$("#btn-browse-epath").onclick = () => runAction(() => browsePathInto("#e-path", "gui.pick_folder"));
$("#btn-edit-delete").onclick = () => runAction(deleteEditProject);
$("#filter-tag").onchange = async (e) => { state.filterTag = e.target.value; await runAction(async () => { await loadTasks(); renderKanban(); renderTagFilter(); }); };
$("#filter-subtasks").onchange = async (e) => { state.subtaskView = e.target.value; await runAction(async () => { renderKanban(); }); };
$("#btn-trash").onclick = () => runAction(async () => {
  state.showTrash = !state.showTrash; // real toggle button (state synced in renderKanban)
  await loadTasks(); renderKanban();
});
$("#log-trash").onchange = async (e) => { state.logTrash = e.target.checked; await runAction(async () => { await loadLogs(); renderLogs(); }); };
$("#feat-trash").onchange = async (e) => { state.featTrash = e.target.checked; await runAction(async () => { await loadFeatures(); renderFeatures(); }); };
$("#btn-reset-filters").onclick = () => runAction(async () => {
  state.filterTag = ""; state.filterPrio = ""; state.taskQuery = "";
  $("#filter-search").value = ""; $("#filter-priority").value = ""; $("#filter-tag").value = "";
  await loadTasks(); renderKanban(); renderTagFilter();
});
$("#filter-priority").onchange = async (e) => { state.filterPrio = e.target.value; await runAction(async () => { await loadTasks(); renderKanban(); }); };
let taskSearchTimer = null;
$("#filter-search").addEventListener("input", (e) => {
  state.taskQuery = e.target.value;
  clearTimeout(taskSearchTimer);
  taskSearchTimer = setTimeout(() => runAction(async () => { await loadTasks(); renderKanban(); }), 250); // debounce: no request per keystroke
});
$("#git-task-filter").onchange = async (e) => { state.gitTaskFilter = e.target.value; await runAction(async () => { await loadGit(); renderGit(); }); };
$("#btn-cancel-task").onclick = closeTaskModal;
$("#btn-save-task").onclick = () => runAction(saveTaskModal);
// two-step delete: first click arms the button, second click deletes for real
$("#btn-delete-task").onclick = () => {
  const id = state.editingTaskId;
  if (id == null) return;
  const btn = $("#btn-delete-task");
  if (armOnce(btn, { confirmKey: "gui.task.delete_confirm", resetKey: "gui.task.delete", ms: ARM_LONG_MS, cls: "btn-danger-armed" })) return;
  runAction(async () => {
    await api.post("/api/tasks/delete", { id });
    notify(T("gui.toast.task_deleted", { id }));
    closeTaskModal();
    await loadAll();
  });
};
// AI verdict on the open task: proposal only — the approve button below is the
// HUMAN decision that actually marks the task done (the API refuses apply=true)
$("#btn-task-verify").onclick = () => runAction(async () => {
  const id = state.editingTaskId;
  if (id == null) return;
  const btn = $("#btn-task-verify");
  btn.disabled = true;
  $("#task-verdict-result").textContent = T("gui.ai.busy");
  try {
    const r = await api.post("/api/ai/task_verdict", { id });
  const v = r.verdict;
  state.taskVerdict = v;
  const t = state.tasks.find((x) => x.id === id);
  $("#task-verdict-result").textContent =
    `${v.seems_complete ? T("verdict.complete") : T("verdict.incomplete")} (${Math.round(v.confidence * 100)}%) — ${v.reasoning}` +
    (v.evidence && v.evidence.length ? "\n• " + v.evidence.join("\n• ") : "");
  // the approve button is shown only for a positive verdict on an unfinished task
  $("#btn-task-verify-approve").classList.toggle("hidden",
    !(v.seems_complete && t && t.status !== "done" && t.status !== "cancelled"));
  } finally {
    btn.disabled = false;
  }
});
$("#btn-task-verify-approve").onclick = () => runAction(async () => {
  const id = state.editingTaskId;
  if (id == null) return;
  // human click = approval: THIS is what marks the task done, not the AI
  await completeTask(id);
  closeTaskModal();
  await loadAll();
});
$("#btn-export").onclick = () => runAction(doExport);
$("#btn-demo").onclick = () => runAction(doDemoSeed);
$("#btn-upd-check").onclick = () => runAction(checkUpdate);
$("#btn-upd-run").onclick = () => runAction(runUpdate);
$("#btn-build").onclick = () => runAction(runBuild);
wireKanbanDnD();
wireSubtaskDnD();
// click a column header to collapse/expand it (persisted) — keeps long projects readable
document.querySelectorAll("#kanban .col").forEach((col) => {
  const h3 = col.querySelector("h3");
  if (h3) h3.addEventListener("click", () => {
    const st = col.dataset.status;
    state.collapsed[st] = !state.collapsed[st];
    renderKanban();
  });
});
$("#stats-project").onchange = async (e) => { state.statsFilters.project = e.target.value; await runAction(async () => { await loadStats(); renderStats(); }); };
$("#stats-days").onchange = async (e) => { state.statsFilters.days = e.target.value; await runAction(async () => { await loadStats(); renderStats(); }); };
$("#stats-group").onchange = async (e) => { state.statsFilters.group = e.target.value; await runAction(async () => { await loadStats(); renderStats(); }); };
$("#btn-session-start").onclick = () => runAction(startSession);
$("#btn-session-stop").onclick = () => runAction(stopSession);

// duration stepper: empty = "auto" (no target). + from auto starts at 25 min,
// − from 5 min goes back to auto. Manual input is sanitized on change:
// rounded to a 5-min step, clamped to [5, 480], anything else -> auto.
const SESS_MIN_STEP = 5, SESS_MIN_MAX = 480, SESS_MIN_DEFAULT = 25;
function setSessionMinutes(v) { // v: int minutes, or null = auto
  $("#session-minutes").value = v == null ? "" : String(v);
}
function sanitizeSessionMinutes() {
  const el = $("#session-minutes");
  const v = parseInt(el.value, 10);
  if (Number.isNaN(v) || v <= 0) { setSessionMinutes(null); return; }
  setSessionMinutes(Math.min(SESS_MIN_MAX, Math.round(v / SESS_MIN_STEP) * SESS_MIN_STEP));
}
function bumpSessionMinutes(delta) {
  const el = $("#session-minutes");
  let v = parseInt(el.value, 10);
  if (Number.isNaN(v)) { // empty = auto: "+" starts at the default 25, "−" stays auto
    setSessionMinutes(delta > 0 ? SESS_MIN_DEFAULT : null);
    return;
  }
  v += delta;
  setSessionMinutes(v <= 0 ? null : Math.min(SESS_MIN_MAX, v));
}
$("#sess-minus").onclick = () => bumpSessionMinutes(-SESS_MIN_STEP);
$("#sess-plus").onclick = () => bumpSessionMinutes(SESS_MIN_STEP);
$("#session-minutes").addEventListener("change", sanitizeSessionMinutes);
$("#session-minutes").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runAction(startSession);
  if (e.key === "ArrowUp") { e.preventDefault(); bumpSessionMinutes(SESS_MIN_STEP); }
  if (e.key === "ArrowDown") { e.preventDefault(); bumpSessionMinutes(-SESS_MIN_STEP); }
});
$("#session-note").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(stopSession); });
$("#btn-add-log").onclick = () => runAction(addLog);
$("#log-content").addEventListener("keydown", (e) => {
  // multiline entry: Enter makes a new line, Ctrl/Cmd+Enter sends it
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runAction(addLog); }
});
let logSearchTimer = null;
$("#log-search").addEventListener("input", (e) => {
  state.logQuery = e.target.value;
  clearTimeout(logSearchTimer);
  logSearchTimer = setTimeout(() => runAction(async () => { await loadLogs(); renderLogs(); }), 250);
});
document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
$("#tl-filter").onchange = async (e) => {
  state.tlFilter = e.target.value;
  await runAction(async () => { await loadTimeline(); renderTimeline(); });
};
$("#btn-tl-refresh").onclick = () => runAction(async () => { await loadTimeline(); renderTimeline(); });
$("#btn-report-week").onclick = () => runAction(downloadReport);
$("#btn-git-link").onclick = () => runAction(gitLink);
$("#btn-git-sync").onclick = () => runAction(gitSync);
$("#btn-git-fetch").onclick = () => runAction(() => gitRemote("fetch"));
$("#btn-git-pull").onclick = () => runAction(() => gitRemote("pull --ff-only"));
$("#btn-git-diff-sel").onclick = () => runAction(gitDiffSelection);
$("#git-sel-all").onchange = (e) => {
  const ch = state.gitChanges;
  if (!ch || !ch.files) return;
  for (const f of ch.files) {
    if (e.target.checked) state.gitSelFiles[f.path] = true;
    else delete state.gitSelFiles[f.path];
  }
  renderGitChanges();
};
$("#btn-git-ignore").onclick = () => runAction(gitIgnore);
$("#btn-git-open").onclick = () => runAction(gitOpenRepo);
$("#btn-git-changes").onclick = () => runAction(async () => {
  state.gitChangesOpen = !state.gitChangesOpen;
  if (state.gitChangesOpen) await gitRefreshChanges(); else renderGitChanges();
});
$("#btn-git-changes-refresh").onclick = () => runAction(gitRefreshChanges);
$("#git-branches").onchange = async (e) => {
  const b = e.target.value;
  if (!b) return;
  // read-only: show the branch's recent commits in the terminal output
  await gitShowDiffCmd(`log --oneline -8 ${b}`);
};
$("#btn-git-run").onclick = () => runAction(gitRun);
$("#git-cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(gitRun); });
$("#btn-git-unlink").onclick = () => runAction(async () => { await api.post("/api/git/unlink", {}); await loadAll(); });
$("#git-unparsed").onchange = async (e) => { state.gitUnparsed = e.target.checked; await runAction(async () => { await loadGit(); renderGit(); }); };
$("#btn-add-feature").onclick = () => runAction(addFeature);
$("#feature-name").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(addFeature); });
$("#btn-chat-send").onclick = () => runAction(chatSend);
$("#chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(chatSend); });
$("#btn-chat-week").onclick = () => runAction(chatWeek);
$("#btn-chat-context").onclick = () => runAction(chatContext);
$("#btn-chat-clear").onclick = () => runAction(clearAiChat);
$("#btn-ai-suggest").onclick = () => runAction(aiSuggest);
$("#cfg-provider").onchange = () => { updateProviderFields(); fillModelPresets(currentModelValue()); state.settingsDirty = true; };
$("#cfg-model").onchange = () => {
  $("#cfg-model-custom").classList.toggle("hidden", $("#cfg-model").value !== "__custom__");
  state.settingsDirty = true;
};
$("#cfg-theme").onchange = () => { // live preview, saved with the rest of the form
  applyTheme($("#cfg-theme").value);
  renderStats._sig = null;
  if (state.tab === "stats") renderStats();
};
document.querySelectorAll("#tab-settings input, #tab-settings select, #tab-settings textarea").forEach((el) =>
  el.addEventListener("input", () => { state.settingsDirty = true; }));
$("#btn-ai-save").onclick = () => runAction(saveAllSettings);
$("#btn-general-save").onclick = () => runAction(saveAllSettings); // save lives in the General card too (AI card is collapsed when the assistant is off)
$("#btn-sidebar-toggle").onclick = () => { state.sidebarCollapsed = !state.sidebarCollapsed; applySidebar(); saveUi(); };
$("#btn-ai-test").onclick = () => runAction(testAiConfig);
$("#btn-backup").onclick = () => runAction(doBackup);
$("#btn-plan").onclick = () => runAction(() => {
  if (state.pendingPlan) {
    // explicit confirmation: second click = create the subtasks (apply).
    // Set the input FIRST: chatPlan reads it synchronously.
    $("#plan-input").value = state.pendingPlan;
    state.pendingPlan = null;
    chatPlan(true);
  } else {
    chatPlan(false);
  }
});

/* ---------- live updates ---------- */

async function pollEvents() {
  if (document.hidden) return; // no point polling (and reloading charts) while the tab is hidden
  try {
    const d = await api.get("/api/events?since=" + state.lastEventId);
    if (d.events.length > 0) {
      await loadAll(); // only advance the cursor once the reload SUCCEEDED,
      state.lastEventId = d.last_id; // otherwise those events would be skipped forever
    } else {
      state.lastEventId = d.last_id;
    }
    $("#conn-status").style.color = "var(--green)";
  } catch {
    $("#conn-status").style.color = "var(--red)";
  }
}

// --- native window chrome (frameless pywebview): custom title-bar controls ---
// In browser mode window.pywebview is absent and #titlebar stays hidden (CSS).
function initWindowControls() {
  const api = window.pywebview && window.pywebview.api;
  if (!api) return;
  document.documentElement.classList.add("native"); // in case the head inline script ran before injection
  const min = $("#btn-win-min"), max = $("#btn-win-max"), close = $("#btn-win-close"), bar = $("#titlebar");
  if (!min || !max || !close || !bar) return;
  min.onclick = () => api.minimize();
  close.onclick = () => api.close();
  const toggleMax = () => {
    const maximized = !!api.toggle_maximize();
    max.textContent = maximized ? "❐" : "▢";
    max.dataset.i18nTitle = maximized ? "gui.win.restore_tt" : "gui.win.max_tt";
    max.title = T(max.dataset.i18nTitle);
  };
  max.onclick = toggleMax;
  // drag the window by the bar: CSS app-region handles it when the backend
  // supports it (the event is then swallowed and never reaches JS); otherwise
  // the Win32 HTCAPTION fallback in api.start_drag() takes over
  bar.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || e.target.closest(".tb-btn")) return;
    if (api.start_drag) api.start_drag();
  });
  bar.addEventListener("dblclick", (e) => { if (!e.target.closest(".tb-btn")) toggleMax(); });
}
initWindowControls();
/* ---------- window-size adaptation ----------
   The resize event fires on maximize/restore too (browser AND pywebview), so one
   debounced handler covers both. Media queries handle raw width; these body
   classes handle "wide" (use the extra horizontal space) and "maximized"
   (window fills the screen: roomier sidebar, taller panels). On small screens
   "maximized" is never set, so the narrow media queries keep full control. */
function updateWinMode() {
  const w = window.innerWidth, h = window.innerHeight;
  const tol = 16; // window chrome + taskbar tolerance
  const wide = w >= 1400;
  const maxed = wide && w >= screen.availWidth - tol && h >= screen.availHeight - tol;
  document.body.classList.toggle("wide", wide);
  document.body.classList.toggle("maximized", maxed);
}
let winModeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(winModeTimer);
  winModeTimer = setTimeout(() => {
    updateWinMode();
    if (tour.active) tourShow(tour.idx); // keep the tour card anchored to its target
  }, 120); // debounce: resize fires continuously while dragging
});
updateWinMode();

window.addEventListener("pywebviewready", initWindowControls);
setTimeout(initWindowControls, 800); // safety net if pywebviewready fired before this script ran

setInterval(tickClock, 1000);
// load the UI language first, then apply it to static labels, then render
api.get("/api/i18n")
  .then((d) => { I18N = d; applyI18n(); })
  .catch(() => {})
  .finally(() => {
    loadUi(); // restore tab + filters before the first render
    // load general settings once at boot too: the Pomodoro checkbox needs
    // pomodoro_focus_min even when the user never opens the settings tab
    loadSettings().catch(() => {});
    loadAll().then(() => {
      switchTab(state.tab);
      // tour flag is read from the DB (origin-proof), not only localStorage
      api.get("/api/tour").then((r) => { state.tourDone = !!r.done; })  // server flag is authoritative (localStorage is only a pre-boot hint)
        .catch(() => {})
        .finally(() => { if (!state.tourDone) startTour(); }); // first launch: guided tour
      // the help shortcut doc must match the number of VISIBLE tabs (AI tab can be hidden)
      const htm = $("#help-tabs-max");
      if (htm) htm.textContent = String(tabOrder().length);
      return api.get("/api/status").then((s) => { state.lastEventId = s.last_event_id || 0; });
    }).catch(() => {}); // a failed boot load must not stay an unhandled rejection
  });
// adaptive polling: fast while visible, slow while the tab/window is hidden
// (the connection dot still updates, but we stop hammering the API)
// 1.5s visible / 10s hidden is plenty -- no need to over-engineer this
const POLL_FAST_MS = 1500, POLL_HIDDEN_MS = 10000;
// UI timers: arm window for destructive buttons, toast display, palette sizes
const ARM_MS = 5000, ARM_LONG_MS = 6000, ARM_SHORT_MS = 4000, TOAST_MS = 6000; // arm delays for 2-step buttons + toast lifetime
const TEXTAREA_MAX_PX = 400; // auto-growing textareas are capped at this height
// estimate overrun thresholds: ±20% around the estimate counts as over/under
const EST_OVER_RATIO = 1.2, EST_UNDER_RATIO = 0.8;
const EVENT_LABEL_SNIPPET = 140; // timeline event label chars before truncation
const ESTIMATES_TOP = 8;         // rows shown in the estimates table
const EST_TITLE_SNIPPET = 34;    // task title chars in the estimates table
const PALETTE_MAX = 40, PALETTE_TASKS = 30, PALETTE_LOGS = 10, DEFAULT_AI_CONTEXT_MAX = 6000;
async function pollLoop() {
  await pollEvents();
  setTimeout(pollLoop, document.hidden ? POLL_HIDDEN_MS : POLL_FAST_MS);
}
pollLoop();

/* ---------- utils ---------- */

// long descriptions: textareas grow with their content while typing (capped,
// then they scroll internally)
document.addEventListener("input", (e) => {
  const el = e.target;
  if (el && el.classList && el.classList.contains("desc-input")) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, TEXTAREA_MAX_PX) + "px";
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- UX polish: toasts, relative time, persisted UI, palette, shortcuts ---------- */

// success toast (green); errors keep going through showError (red)
function notify(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden", "out");
  t.classList.add("ok");
  clearTimeout(notify._h);
  notify._h = setTimeout(() => {
    t.classList.add("out"); // slide/fade out before hiding
    setTimeout(() => { t.classList.add("hidden"); t.classList.remove("ok", "out"); }, 170);
  }, TOAST_MS);
}

// "il y a 5 min" style label; the absolute timestamp stays available in the tooltip
function timeAgo(iso) {
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return escapeHtml(iso);
  const s = Math.max(0, Math.floor((Date.now() - d) / 1000));
  const abs = iso.slice(0, 16).replace("T", " ");
  let txt;
  if (s < 45) txt = T("gui.time.now");
  else if (s < 3600) txt = T("gui.time.min", { n: Math.round(s / 60) });
  else if (s < 86400) txt = T("gui.time.hour", { n: Math.round(s / 3600) });
  else if (s < 604800) txt = T("gui.time.day", { n: Math.round(s / 86400) });
  else txt = T("gui.time.week", { n: Math.round(s / 604800) });
  return `<span title="${escapeHtml(abs)}">${txt}</span>`;
}

/* ----- persisted UI state: tab + filters survive a reload (localStorage only) ----- */
const UI_KEY = "devdash-ui-v1";
function saveUi() {
  try {
    localStorage.setItem(UI_KEY, JSON.stringify({
      tab: state.tab, filterTag: state.filterTag, filterPrio: state.filterPrio,
      taskQuery: state.taskQuery, logQuery: state.logQuery,
      gitTaskFilter: state.gitTaskFilter, gitUnparsed: state.gitUnparsed,
      statsFilters: state.statsFilters, subtaskView: state.subtaskView,
      collapsed: state.collapsed, tlFilter: state.tlFilter, sidebarCollapsed: state.sidebarCollapsed,
      tourDone: state.tourDone,
    }));
  } catch { /* storage unavailable (private mode): non-fatal */ }
}
function loadUi() {
  try {
    const u = JSON.parse(localStorage.getItem(UI_KEY) || "null");
    if (!u) return;
    if (typeof u.tab === "string") state.tab = u.tab;
    for (const k of ["filterTag", "filterPrio", "taskQuery", "logQuery", "gitTaskFilter", "tlFilter"]) {
      if (typeof u[k] === "string") state[k] = u[k];
    }
    if (typeof u.tlFilter === "string") $("#tl-filter").value = state.tlFilter;
    if (u.subtaskView === "stacked" || u.subtaskView === "flat") state.subtaskView = u.subtaskView;
    if (u.collapsed && typeof u.collapsed === "object") state.collapsed = u.collapsed;
    if (typeof u.gitUnparsed === "boolean") state.gitUnparsed = u.gitUnparsed;
    if (typeof u.sidebarCollapsed === "boolean") state.sidebarCollapsed = u.sidebarCollapsed;
    if (u.tourDone === true) state.tourDone = true;
    applySidebar();
    if (u.statsFilters && typeof u.statsFilters === "object")
      state.statsFilters = { ...state.statsFilters, ...u.statsFilters };
    $("#filter-search").value = state.taskQuery;
    $("#filter-priority").value = state.filterPrio;
    $("#log-search").value = state.logQuery;
    $("#git-unparsed").checked = state.gitUnparsed;
    $("#filter-subtasks").value = state.subtaskView;
  } catch { /* corrupted entry: start fresh */ }
}

/* ----- first-launch guided tour: 7 steps, spotlight on the target element ----- */
const TOUR_STEPS = [null, "#sidebar", "#task-toolbar", "#kanban", "#session-panel", "#tabs", null];
const tour = { active: false, idx: 0 };

function tourShow(i) {
  tour.idx = i;
  document.querySelectorAll(".tour-hl").forEach((el) => el.classList.remove("tour-hl"));
  const sel = TOUR_STEPS[i];
  let rect = null;
  if (sel) {
    const el = $(sel);
    if (el) { el.classList.add("tour-hl"); rect = el.getBoundingClientRect(); }
  }
  $("#tour-title").textContent = T(`tour.step${i + 1}.t`);
  $("#tour-text").textContent = T(`tour.step${i + 1}.d`);
  $("#tour-step-label").textContent = T("tour.step_label", { n: i + 1, total: TOUR_STEPS.length });
  $("#tour-next").textContent = i === TOUR_STEPS.length - 1 ? T("tour.finish") : T("tour.next");
  $("#tour-prev").classList.toggle("hidden", i === 0);
  // anchor the card next to the highlighted element (clamped to the viewport);
  // welcome/final steps (no target) are centered
  const card = $("#tour-card");
  card.style.transform = "";
  if (rect) {
    const CARD_W = 340, CARD_H = 240;
    let top = rect.bottom + 12;
    if (top + CARD_H > window.innerHeight - 8) top = Math.max(8, rect.top - CARD_H - 12);
    const left = Math.min(Math.max(8, rect.left), window.innerWidth - CARD_W - 8);
    card.style.top = top + "px"; card.style.left = left + "px";
  } else {
    card.style.top = "16vh"; card.style.left = "50%"; card.style.transform = "translateX(-50%)";
  }
  $("#tour").classList.remove("hidden");
}

function tourEnd() {
  tour.active = false;
  $("#tour").classList.add("hidden");
  document.querySelectorAll(".tour-hl").forEach((el) => el.classList.remove("tour-hl"));
  state.tourDone = true; saveUi(); // shown once; replayable from the help modal (?)
  // server-side flag too: localStorage is per-origin and the GUI port can change
  api.post("/api/tour", { done: true }).catch(() => {});
}

function startTour() { tour.active = true; tourShow(0); }

$("#tour-next").onclick = () => (tour.idx === TOUR_STEPS.length - 1 ? tourEnd() : tourShow(tour.idx + 1));
$("#tour-prev").onclick = () => { if (tour.idx > 0) tourShow(tour.idx - 1); };
$("#tour-skip").onclick = () => tourEnd();
$("#btn-tour").onclick = () => { closeHelp(); startTour(); }; // replay from the help modal

/* ----- command palette (Ctrl+K): commands + tasks + logs + features + projects ----- */
const palette = { open: false, items: [], idx: 0 };

function paletteCommands() {
  const cmds = [
    { sectionKey: "cmd", label: T("gui.cmd.new_task"), run: () => { switchTab("tasks"); $("#new-task-title").focus(); } },
    { sectionKey: "cmd", label: T("gui.cmd.new_project"), run: () => openModal() },
    { sectionKey: "cmd", label: T("gui.cmd.connect"), run: () => { openModal(); $("#f-path").focus(); } },
    { sectionKey: "cmd", label: T("gui.cmd.session"), run: () => runAction(async () => { state.session ? await stopSession() : await startSession(); }) },
    { sectionKey: "cmd", label: T("gui.cmd.git_sync"), run: () => runAction(gitSync) },
    { sectionKey: "cmd", label: T("gui.cmd.backup"), run: () => { switchTab("settings"); runAction(doBackup); } },
    { sectionKey: "cmd", label: T("gui.cmd.export"), run: () => { switchTab("settings"); runAction(doExport); } },
    { sectionKey: "cmd", label: T("gui.cmd.update"), run: () => { switchTab("settings"); runAction(checkUpdate); } },
    { sectionKey: "cmd", label: T("gui.cmd.help"), run: () => openHelp() },
  ];
  for (const tab of ["tasks", "log", "git", "features", "stats", "ai", "settings"]) {
    if (tab === "ai" && !state.ai.enabled) continue; // hidden tab = no shortcut entry
    cmds.push({ sectionKey: "cmd", label: T("gui.cmd.goto." + tab), run: () => switchTab(tab) });
  }
  return cmds;
}

function paletteItems(query) {
  const q = query.trim().toLowerCase();
  const match = (s) => !q || s.toLowerCase().includes(q);
  // one descriptor per palette section: {key, items: [{label, run}]}
  const sections = [
    { key: "cmd", items: paletteCommands().map((c) => ({ label: c.label, run: c.run })) },
    { key: "tasks", items: state.tasks
        .filter((x) => x.status !== "done" && x.status !== "cancelled").slice(0, PALETTE_TASKS)
        .map((t) => ({ label: T("gui.cmd.open_task", { id: t.id, title: t.title }),
                       run: () => { switchTab("tasks"); openTaskModal(t).catch(() => {}); } })) },
    { key: "logs", items: (state.logs || []).slice(0, PALETTE_LOGS)
        .map((e) => ({ label: e.content.slice(0, 80), run: () => {
          switchTab("log");
          state.logQuery = e.content.slice(0, 30);
          $("#log-search").value = state.logQuery;
          runAction(async () => { await loadLogs(); renderLogs(); });
        } })) },
    { key: "feats", items: (state.features || [])
        .map((f) => ({ label: T("gui.cmd.open_feat", { name: f.name }), run: () => {
          switchTab("features");
          state.featOpen = f.name;
          runAction(async () => { await loadFeatures(); renderFeatures(); });
        } })) },
    { key: "projs", items: state.projects.filter((x) => !x.archived) // archived projects are not activatable
        .map((p) => ({ label: T("gui.cmd.set_active", { name: p.name }), run: () => runAction(async () => {
          await api.post("/api/projects/set-active", { ref: p.slug });
          notify(T("gui.toast.project_active", { name: p.name }));
          await loadAll();
        }) })) },
  ];
  const items = [];
  for (const s of sections)
    for (const it of s.items)
      if (match(it.label)) items.push({ sectionKey: s.key, section: "gui.palette." + s.key, label: it.label, run: it.run });
  return items.slice(0, PALETTE_MAX);
}

function openPalette() {
  rememberFocus();
  palette.open = true;
  $("#palette").classList.remove("hidden");
  const inp = $("#palette-input");
  inp.value = "";
  renderPalette();
  inp.focus();
}
function closePalette() { palette.open = false; hideAnimated($("#palette")); restoreFocus(); }

function renderPalette() {
  palette.items = paletteItems($("#palette-input").value);
  palette.idx = 0;
  const ul = $("#palette-list");
  ul.innerHTML = "";
  if (!palette.items.length) {
    ul.innerHTML = `<li class="dim palette-empty">${T("gui.palette.empty")}</li>`;
    return;
  }
  let lastSection = null;
  palette.items.forEach((it, i) => {
    if (it.sectionKey !== lastSection) {
      lastSection = it.sectionKey;
      const h = document.createElement("li");
      h.className = "palette-section";
      h.textContent = T(it.section);
      ul.appendChild(h);
    }
    const li = document.createElement("li");
    li.className = "palette-item" + (i === palette.idx ? " active" : "");
    li.textContent = it.label;
    li.onclick = () => { closePalette(); it.run(); };
    ul.appendChild(li);
  });
}

function paintPaletteActive() {
  document.querySelectorAll("#palette-list .palette-item")
    .forEach((el, i) => {
      el.classList.toggle("active", i === palette.idx);
      if (i === palette.idx) el.scrollIntoView({ block: "nearest" }); // keep the active item visible
    });
}

// returns true when the event was consumed by the palette
function paletteKey(e) {
  if (!palette.open) return false;
  if (e.key === "Escape") { closePalette(); return true; }
  if (e.key === "ArrowDown") { palette.idx = Math.min(palette.items.length - 1, palette.idx + 1); paintPaletteActive(); e.preventDefault(); return true; }
  if (e.key === "ArrowUp") { palette.idx = Math.max(0, palette.idx - 1); paintPaletteActive(); e.preventDefault(); return true; }
  if (e.key === "Enter") {
    const it = palette.items[palette.idx];
    if (it) { closePalette(); it.run(); }
    e.preventDefault();
    return true;
  }
  return false;
}

/* ----- help modal ----- */
function openHelp() { rememberFocus(); $("#modal-help").classList.remove("hidden"); }
function closeHelp() { hideAnimated($("#modal-help")); restoreFocus(); }
$("#btn-close-help").onclick = closeHelp;

/* ----- global keyboard shortcuts (ignored while typing in a field) ----- */
// built from the VISIBLE tabs so the numbers stay consecutive when the AI tab is hidden
function tabOrder() {
  return ["tasks", "log", "git", "features", "stats", "timeline", "ai", "settings"]
    .filter((t) => t !== "ai" || state.ai.enabled);
}
document.addEventListener("keydown", (e) => {
  if (paletteKey(e)) return;
  if (modalTrapTab(e)) return; // keep Tab inside the open modal/palette
  const typing = e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" ||
                             e.target.tagName === "SELECT" || e.target.isContentEditable);
  if (e.key === "Escape") {
    if (tour.active) return tourEnd(); // tour is the topmost layer
    // close the topmost open layer
    if (!$("#modal-task").classList.contains("hidden")) return closeTaskModal();
    if (!$("#modal-feat").classList.contains("hidden")) return closeFeatModal();
    if (!$("#modal-edit").classList.contains("hidden")) return closeEditModal();
    if (!$("#modal").classList.contains("hidden")) return closeModal();
    if (!$("#modal-help").classList.contains("hidden")) return closeHelp();
    return;
  }
  // while a modal is open, single-key shortcuts stay out of the way (except Esc above)
  const modalOpen = ["#modal", "#modal-edit", "#modal-task", "#modal-feat", "#modal-help"]
    .some((sel) => !$(sel).classList.contains("hidden"));
  if (modalOpen) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    palette.open ? closePalette() : openPalette();
    return;
  }
  if (typing) return; // plain keys belong to the focused field
  switch (e.key) {
    case "?": openHelp(); break;
    case "n": switchTab("tasks"); $("#new-task-title").focus(); e.preventDefault(); break;
    case "/": switchTab("tasks"); $("#filter-search").focus(); e.preventDefault(); break;
    case "s": runAction(async () => { state.session ? await stopSession() : await startSession(); }); break;
    case "r": runAction(loadAll); break;
    case "1": case "2": case "3": case "4": case "5": case "6": case "7": case "8": {
      const tab = tabOrder()[Number(e.key) - 1];
      if (tab) switchTab(tab);
      break;
    }
  }
});

/* ----- palette input wiring ----- */
$("#palette-input").addEventListener("input", renderPalette);
$("#palette").addEventListener("mousedown", (e) => { if (e.target.id === "palette") closePalette(); });

/* ----- session ETA: planned end time when a duration was set ----- */
setInterval(() => {
  const eta = $("#session-eta");
  if (!eta) return;
  if (state.session && state.session.planned_min) {
    const end = new Date(new Date(state.session.started_at).getTime() + state.session.planned_min * 60000);
    eta.textContent = T("gui.sess.eta", {
      time: String(end.getHours()).padStart(2, "0") + ":" + String(end.getMinutes()).padStart(2, "0"),
    });
    eta.classList.remove("hidden");
  } else {
    eta.classList.add("hidden");
  }
}, 1000);

/* ----- copy the task id from the edit modal ----- */
$("#btn-copy-task-id").onclick = () => {
  const id = state.editingTaskId;
  if (id == null) return;
  const txt = "#" + id;
  if (navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(txt).then(() => notify(T("gui.copied")), () => notify(T("gui.copied")));
  else notify(T("gui.copied"));
};

/* ---------- finishing touches: chart PNG export + open the data folder ---------- */

// download all stats charts as one PNG (charts render on a dark canvas already)
$("#btn-stats-png").onclick = () => runAction(async () => {
  const ids = ["chart-time", "chart-done", "chart-tags", "chart-progress", "chart-projects"];
  const canvases = ids.map((id) => document.getElementById(id)).filter((c) => c && c.width > 0);
  if (!canvases.length) return;
  const w = Math.max(...canvases.map((c) => c.width));
  const h = canvases.reduce((a, c) => a + c.height + 16, 0);
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const ctx = out.getContext("2d");
  ctx.fillStyle = "#0f1115";
  ctx.fillRect(0, 0, w, h);
  let y = 0;
  for (const c of canvases) { ctx.drawImage(c, 0, y); y += c.height + 16; }
  const a = document.createElement("a");
  a.download = "devdash-stats.png";
  a.href = out.toDataURL("image/png");
  a.click();
  notify(T("gui.stats.png_done"));
});

// reveal DEVDASH_HOME in the OS file explorer (user-initiated local action)
$("#btn-open-home").onclick = () => runAction(async () => {
  const r = await api.post("/api/open-home", { open: true });
  notify(T("gui.toast.open_home", { path: r.path }));
});
