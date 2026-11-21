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
  gitTaskFilter: "",
  editingTaskId: null,
  editingFeatId: null, // feature currently open in the edit modal
  featOpen: null, // feature name currently expanded
  lastEventId: 0,
  tab: "tasks",
  gitStatus: null,
  gitCommits: [],
  gitUnparsed: false,
  features: [],
  statsFilters: { project: "", days: "14", group: "day" }, // "" = active project
  ai: { enabled: false },
  chat: [],
  stats: null,
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
function applyI18n() {
  document.documentElement.lang = I18N.lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = T(el.dataset.i18n); });
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
  await Promise.all([
    loadTasks(), loadTags(), loadSession(), loadSessions(), loadLogs(), loadGit(),
    loadFeatures(), loadAiStatus(), loadStats(), ...settingsLoads,
    api.get("/api/status").then((s) => { state.status = s; }),
  ]);
  render();
}

async function loadTasks() {
  const q = new URLSearchParams({ all: "1" });
  if (state.filterTag) q.set("tag", state.filterTag);
  if (state.filterPrio) q.set("priority", state.filterPrio);
  if (state.taskQuery) q.set("q", state.taskQuery);
  state.tasks = (await api.get("/api/tasks?" + q.toString())).tasks;
}

async function loadTags() {
  // unfiltered list: the filter select must keep showing every tag even while
  // one is selected (otherwise you can never switch to another tag directly)
  const q = new URLSearchParams();
  if (state.statsFilters.project) q.set("project", state.statsFilters.project);
  state.allTags = (await api.get("/api/tags?" + q.toString())).tags;
}

async function loadSession() {
  const s = await api.get("/api/session/current");
  state.session = s.running ? s.session : null;
}

async function loadSessions() {
  state.recentSessions = (await api.get("/api/sessions?limit=8")).sessions;
}

async function loadLogs() {
  const q = new URLSearchParams({ limit: "200" });
  if (state.logQuery) q.set("q", state.logQuery);
  state.logs = (await api.get("/api/logs?" + q.toString())).entries;
}

async function loadGit() {
  try {
    state.gitStatus = await api.get("/api/git/status");
    const q = new URLSearchParams({ limit: "100" });
    if (state.gitUnparsed) q.set("unparsed", "1");
    if (state.gitTaskFilter) q.set("task", state.gitTaskFilter);
    state.gitCommits = (await api.get("/api/git/commits?" + q.toString())).commits;
    state.gitError = null;
  } catch (e) {
    state.gitStatus = { linked: false };
    state.gitCommits = [];
    state.gitError = e.message; // show the real error, not a fake "no repo linked"
  }
}

async function loadFeatures() {
  try {
    state.features = (await api.get("/api/features")).features;
  } catch {
    state.features = [];
  }
}

async function loadStats() {
  try {
    const f = state.statsFilters;
    const q = new URLSearchParams({ days: f.days, group: f.group });
    if (f.project) q.set("project", f.project); // "" -> active project (server default), "all" -> aggregated
    state.stats = await api.get("/api/stats?" + q.toString());
    renderStatsFilters();
  } catch (e) {
    state.stats = null;
    // silent empty tab is confusing: say it (only when the user is looking at it)
    if (state.tab === "stats") showError(e.message);
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
  const banner = $("#ai-banner");
  if (state.ai.enabled && state.ai.cloud) {
    banner.classList.remove("hidden");
    banner.textContent = T("gui.ai_banner", { provider: state.ai.provider });
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
  $("#cfg-model").value = cfg.ai_model || "";
  $("#cfg-base-url").value = cfg.ai_base_url || "";
  $("#cfg-api-key").value = ""; // write-only: a stored key is never sent back to the client
  $("#cfg-api-key-set").classList.toggle("hidden", !cfg.api_key_set);
  $("#cfg-cli").value = cfg.ai_cli_command || "";
  $("#cfg-context-max").value = cfg.ai_context_max_chars || 6000;
  const fl = cfg.ai_feature_flags || {};
  $("#flag-chat").checked = !!fl.chat;
  $("#flag-summarize").checked = !!fl.summarize;
  $("#flag-plan").checked = !!fl.plan;
  $("#flag-verdict").checked = !!fl.feature_verdict;
  $("#flag-task-verdict").checked = !!fl.task_verdict;
  $("#cfg-path").textContent = T("gui.set.config_path", { path: cfg.config_path || "" });
  updateProviderFields();
}

function currentProviderMeta() {
  return (state.aiProviders || []).find((p) => p.id === $("#cfg-provider").value) || {};
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
    feature_verdict: $("#flag-verdict").checked,
    task_verdict: $("#flag-task-verdict").checked,
  };
  return {
    ai_enabled: $("#cfg-enabled").checked,
    ai_provider: $("#cfg-provider").value,
    ai_model: $("#cfg-model").value,
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
    if (!state.settingsDirty) fillSettingsForm();
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
  $("#cfg-patterns").value = (g.commit_patterns || []).join("\n");
  $("#set-home").textContent = s.home || "";
}

function readGeneralForm() {
  return {
    lang: $("#cfg-lang").value,
    pomodoro_focus_min: parseInt($("#cfg-pomo-focus").value, 10),
    pomodoro_break_min: parseInt($("#cfg-pomo-break").value, 10),
    git_watch_interval_sec: parseInt($("#cfg-git-watch").value, 10),
    gui_port: parseInt($("#cfg-port").value, 10),
    commit_patterns: $("#cfg-patterns").value.split("\n").map((s) => s.trim()).filter(Boolean),
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
  fillSettingsForm();
  fillAiForm();
  $("#cfg-status").textContent = T("gui.set.saved");
  await loadAiStatus(); // show/hide the AI tab + cloud banner immediately
  // language applies hot: reload the catalog and retranslate the static labels
  if (body.lang && body.lang !== I18N.lang) {
    try {
      I18N = await api.get("/api/i18n");
      applyI18n();
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

async function loadUpdateStatus() {
  try {
    state.upd = await api.get("/api/update/status");
    const u = state.upd;
    $("#upd-info").textContent = T("gui.upd.info", {
      version: u.version || "?", mode: u.mode || "?", head: u.head || "-",
    });
  } catch { /* leave as-is */ }
}

async function checkUpdate() {
  const st = $("#upd-status");
  st.textContent = "";
  try {
    const r = await api.post("/api/update/check", {});
    if (r.error) st.textContent = T("gui.upd.fail", { error: r.error });
    else if (r.update_available)
      st.textContent = T("gui.upd.available", { local: r.local, remote: r.remote, behind: r.behind });
    else st.textContent = T("gui.upd.uptodate", { head: r.local || "-" });
  } catch (e) {
    st.textContent = T("gui.upd.fail", { error: e.message });
  }
}

async function runUpdate() {
  const st = $("#upd-status");
  const btn = $("#btn-upd-run");
  // two-step confirm, same pattern as delete/demo: first click arms, second runs
  if (btn.dataset.armed !== "1") {
    btn.dataset.armed = "1";
    btn.textContent = T("gui.upd.confirm");
    st.textContent = "";
    setTimeout(() => { btn.dataset.armed = ""; btn.textContent = T("gui.upd.update"); }, 6000);
    return;
  }
  btn.dataset.armed = "";
  btn.textContent = T("gui.upd.update");
  btn.disabled = true;
  st.textContent = T("gui.upd.running");
  try {
    const r = await api.post("/api/update", { reinstall: true, confirm: true });
    st.textContent = r.ok
      ? T("gui.upd.done", { head: r.head || "-" })
      : T("gui.upd.fail", { error: r.error || "?" });
    await loadUpdateStatus();
  } catch (e) {
    st.textContent = T("gui.upd.fail", { error: e.message });
  } finally {
    btn.disabled = false;
  }
}

async function doDemoSeed() {
  const btn = $("#btn-demo");
  const st = $("#backup-status");
  if (btn.dataset.armed !== "1") {
    btn.dataset.armed = "1";
    btn.textContent = T("gui.set.demo_confirm");
    setTimeout(() => { btn.dataset.armed = ""; btn.textContent = T("gui.set.demo_btn"); }, 5000);
    return;
  }
  btn.dataset.armed = "";
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

function render() {
  renderPanel("projects", [state.projects, state.active], renderProjects);
  renderPanel("header", [state.projects, state.active, state.tasks, state.status], renderHeader);
  renderPanel("kanban", [state.tasks, state.features], renderKanban);
  renderPanel("tagfilter", [state.allTags, state.filterTag], renderTagFilter);
  renderPanel("session", [state.session, state.tasks], renderSessionPanel);
  renderPanel("sessions", state.sessions, renderSessions);
  renderPanel("logs", [state.logs, state.logQuery], renderLogs);
  renderPanel("git", [state.gitStatus, state.gitCommits, state.gitUnparsed, state.gitTaskFilter], renderGit);
  renderPanel("features", state.features, renderFeatures);
  renderPanel("chat", [state.chat, state.ai], renderChat);
  renderPanel("stats", [state.stats, state.statsFilters], renderStats);
  renderPanel("ui", [state.tab, state.filterTag, state.filterPrio, state.taskQuery, state.logQuery,
                     state.gitTaskFilter, state.gitUnparsed, state.statsFilters], saveUi);
}

// project colors end up in a style attribute: only accept #rrggbb (the API
// validates on write too — this is defense in depth against a hand-edited DB)
function cssColor(c) {
  return /^#[0-9a-fA-F]{6}$/.test(c || "") ? c : "#8a8f98";
}

function renderProjects() {
  const ul = $("#project-list");
  ul.innerHTML = "";
  for (const p of state.projects.filter((x) => !x.archived)) {  // archived hidden from the sidebar
    const li = document.createElement("li");
    if (p.slug === state.active) li.classList.add("active");
    li.innerHTML =
      `<span class="dot" style="background:${cssColor(p.color)}"></span>` +
      `<span>${escapeHtml(p.name)}</span>` +
      `<span class="meta">${T("engine." + p.engine)}</span>` +
      `<button class="proj-edit" title="${T("gui.proj.edit_tt")}">✎</button>`;
    li.title = p.slug + " — " + T("pstatus." + p.status);
    li.onclick = () => runAction(async () => {
      await api.post("/api/projects/set-active", { ref: p.slug });
      notify(T("gui.toast.project_active", { name: p.name }));
      await loadAll();
    });
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
  h.innerHTML =
    `<span class="dot" style="background:${cssColor(p.color)}"></span>` +
    `<h1 id="header-proj" class="clickable" title="${T("gui.proj.edit_tt")}">${escapeHtml(p.name)}</h1>` +
    `<span class="sub">${T("gui.header.sub", { engine: T("engine." + p.engine), status: T("pstatus." + p.status), open })}${prog}${today}${streak}</span>`;
  const hp = $("#header-proj");
  if (hp) hp.onclick = () => openEditModal(p); // click the project name in the header to edit it
  const sel = $("#session-task");
  const inProgress = state.tasks.filter((t) => t.status === "in_progress");
  const current = sel.value;
  sel.innerHTML = `<option value="">${T("gui.no_task")}</option>` +
    inProgress.map((t) => `<option value="${t.id}">#${t.id} ${escapeHtml(t.title)}</option>`).join("");
  if (inProgress.some((t) => String(t.id) === current)) sel.value = current;
}

function renderKanban() {
  const statuses = ["todo", "in_progress", "done", "cancelled"]; // cancelled get their own column (reopenable)
  for (const status of statuses) {
    const col = $("#col-" + status);
    if (!col) continue;
    const list = state.tasks.filter((t) => t.status === status);
    $("#count-" + status).textContent = list.length;
    col.innerHTML = "";
    for (const t of list) col.appendChild(taskCard(t));
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

function taskCard(t) {
  const card = document.createElement("div");
  card.className = "card " + t.status;
  card.draggable = true; // drag & drop between kanban columns
  card.dataset.taskId = t.id;
  card.tabIndex = 0; // keyboard: Enter/Space opens the edit modal
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `#${t.id} ${t.title}`);
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openTaskModal(t); }
  });
  const tags = t.tags.map((x) => `<span class="tag">${escapeHtml(x)}</span>`).join("");
  const est = t.estimate_min ? `<span class="est">${T("gui.est", { n: t.estimate_min })}</span>` : "";
  const subs = state.tasks.filter((x) => x.parent_id === t.id);
  const subsDone = subs.filter((x) => x.status === "done").length;
  const subBadge = subs.length
    ? `<span class="tag dim" title="${T("gui.task.subtasks", { done: subsDone, total: subs.length })}">☑ ${subsDone}/${subs.length}</span>` : "";
  const parentRef = t.parent_id ? `<span class="tag dim">${T("gui.task.parent_ref", { id: t.parent_id })}</span>` : "";
  const feat = t.feature_id ? `<span class="tag dim">⚑ #${t.feature_id}</span>` : "";
  const actions = [];
  if (t.status === "todo") actions.push(`<button class="btn btn-sm" data-act="in_progress">${T("gui.task.start")}</button>`);
  if (t.status === "in_progress") {
    actions.push(`<button class="btn btn-sm" data-act="done">${T("gui.task.done")}</button>`);
    actions.push(`<button class="btn btn-sm" data-act="todo">${T("gui.task.todo")}</button>`);
  }
  if (t.status === "done") actions.push(`<button class="btn btn-sm" data-act="todo">${T("gui.task.reopen")}</button>`);
  if (t.status === "cancelled") actions.push(`<button class="btn btn-sm" data-act="todo">${T("gui.task.reopen")}</button>`);
  if (t.status !== "cancelled" && t.status !== "done") actions.push(`<button class="btn btn-sm" data-act="cancelled">${T("gui.task.cancel")}</button>`);
  card.innerHTML =
    `<div class="title">#${t.id} ${escapeHtml(t.title)}</div>` +
    `<div class="row"><span class="prio prio-${t.priority}">${T("prio." + t.priority)}</span>${tags}${est}${subBadge}${parentRef}${feat}</div>` +
    `<div class="actions">${actions.join("")}</div>`;
  card.querySelectorAll("button[data-act]").forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); runAction(async () => {
      await api.post("/api/tasks/status", { id: t.id, status: b.dataset.act });
      const toastKey = { done: "gui.toast.task_done", in_progress: "gui.toast.task_started",
        todo: "gui.toast.task_reopened", cancelled: "gui.toast.task_cancelled" }[b.dataset.act];
      if (toastKey) notify(T(toastKey, { id: t.id }));
      await loadAll();
    }); };
  });
  // click on the card (not on a button) opens the edit modal
  card.onclick = () => openTaskModal(t);
  card.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", String(t.id));
    e.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => card.classList.remove("dragging"));
  return card;
}

/* ---------- kanban drag & drop: drop a card on a column to change status ---------- */

function wireKanbanDnD() {
  document.querySelectorAll("#kanban .col").forEach((col) => {
    const target = col.querySelector(".cards");
    // enter/leave counter: dragleave fires when entering a child card too,
    // which made the hover highlight flicker
    col.addEventListener("dragenter", (e) => { e.preventDefault(); col._dragDepth = (col._dragDepth || 0) + 1; col.classList.add("drop-hover"); });
    col.addEventListener("dragover", (e) => e.preventDefault());
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

function openTaskModal(t) {
  rememberFocus();
  state.editingTaskId = t.id;
  // set only the text span: textContent on the h3 would destroy the copy button
  $("#task-modal-title-text").textContent = T("gui.task.edit_title", { id: t.id });
  $("#t-title").value = t.title;
  $("#t-desc").value = t.description || "";
  $("#t-priority").value = t.priority;
  $("#t-tags").value = (t.tags || []).join(", ");
  $("#t-est").value = t.estimate_min || "";
  // parent select: all tasks of the project except this one and its descendants
  const others = state.tasks.filter((x) => x.id !== t.id);
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
}
function closeTaskModal() { $("#modal-task").classList.add("hidden"); state.editingTaskId = null; restoreFocus(); }

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

function tickClock() {
  if (!state.session) return;
  const started = new Date(state.session.started_at).getTime();
  $("#session-clock").textContent = fmtHms((Date.now() - started) / 1000);
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
      `<span class="log-tools"><button class="mini-btn log-edit" title="${T("gui.log.edit_tt")}">✎</button>` +
      `<button class="mini-btn log-del" title="${T("gui.log.del_tt")}">🗑</button></span></div>` +
      `<div class="log-content">${escapeHtml(e.content)}</div>`;
    // inline edit: swap the content div for an input, save on Enter/blur
    li.querySelector(".log-edit").onclick = () => {
      const div = li.querySelector(".log-content");
      const input = document.createElement("input");
      input.type = "text";
      input.className = "log-edit-input";
      input.value = e.content;
      div.replaceWith(input);
      input.focus();
      let cancelled = false; // Escape cancels instead of saving via the induced blur
      const save = () => runAction(async () => {
        if (cancelled || !input.isConnected) return; // Escape removed it / already saved
        const v = input.value.trim();
        if (v && v !== e.content) await api.post("/api/logs/update", { id: e.id, content: v });
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
        delBtn.dataset.armed = "1";
        delBtn.textContent = "⚠";
        delBtn.title = T("gui.log.del_confirm");
        setTimeout(() => { delBtn.dataset.armed = ""; delBtn.textContent = "🗑"; }, 5000);
      }
    };
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = `<li class="dim">${T("gui.log.empty")}</li>`;
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
  } else {
    const last = st.last_commit;
    box.innerHTML =
      `${T("gui.git.repo")} <span class="mono">${escapeHtml(st.repo_path || "")}</span> · ${T("gui.git.branch")} <b>${escapeHtml(st.branch || "?")}</b> · ` +
      `${T("gui.git.commits_today", { n: st.commits_today })} · ${T("gui.git.cache", { n: st.cached_commits })}` +
      (last ? `<br>${T("gui.git.last", { short: escapeHtml(last.short), msg: escapeHtml(last.message) })}` : "");
  }
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
      `<span class="mono">${escapeHtml(c.short)}</span> ` +
      `<span class="dim">${timeAgo(c.committed_at)}</span> ` +
      `<span class="dim">${escapeHtml(c.author)}</span> — ${escapeHtml(c.message)} ${link}`;
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = `<li class="dim">${T("gui.git.empty")}</li>`;
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

/* ---------- features ---------- */

async function renderFeatures() {
  const ul = $("#feature-list");
  ul.innerHTML = "";
  for (const f of state.features || []) {
    const li = document.createElement("li");
    li.className = "feature-card status-" + f.status;
    const p = f.progress || { total: 0, done: 0, pct: 0 };
    const bar = p.total ? Math.round(p.pct) : 0;
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
          const r = await api.post("/api/ai/verdict", { ref: f.name });
          state.chat.push({ role: "assistant",
            content: T("gui.verdict.chat", { name: f.name,
              verdict: r.verdict.seems_complete ? T("verdict.complete") : T("verdict.incomplete"),
              pct: (r.verdict.confidence * 100).toFixed(0) }) + `\n${r.verdict.reasoning}` +
              (r.proposed ? T("gui.verdict.proposed") : "") });
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
        delBtn.dataset.armed = "1";
        delBtn.textContent = "?";
        setTimeout(() => { delBtn.dataset.armed = ""; delBtn.textContent = "🗑"; }, 5000);
      }
    };
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = `<li class="dim">${T("gui.feat.empty")}</li>`;
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
function closeFeatModal() { $("#modal-feat").classList.add("hidden"); state.editingFeatId = null; restoreFocus(); }

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
  if (btn.dataset.armed !== "1") {
    btn.dataset.armed = "1";
    btn.textContent = T("gui.feat.delete_confirm");
    btn.classList.add("btn-danger-armed");
    setTimeout(() => { btn.dataset.armed = ""; btn.textContent = T("gui.edit.delete"); btn.classList.remove("btn-danger-armed"); }, 6000);
    return;
  }
  runAction(async () => {
    await api.post("/api/features/delete", { ref: f ? f.name : String(id) });
    notify(T("gui.toast.feat_deleted", { name: f ? f.name : id }));
    closeFeatModal();
    await loadAll();
  });
};

/* ---------- stats & charts ---------- */

const charts = {};
const CHART_COLORS = ["#7c5cff", "#3fb27f", "#d9a13b", "#e5534b", "#38bdf8", "#f472b6", "#a3e635", "#fb923c"];

function chartDefaults() {
  if (window.Chart) {
    Chart.defaults.color = "#9aa3b2";
    Chart.defaults.borderColor = "#262c38";
    Chart.defaults.font.family = "Segoe UI, system-ui, sans-serif";
  }
}

function makeBar(canvasId, labels, data, label, color) {
  if (!window.Chart) return;
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: { labels, datasets: [{ label, data,
      backgroundColor: Array.isArray(color) ? color : color, borderRadius: 4 }] },
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

function renderStats() {
  const s = state.stats;
  if (!s) return;
  if (state.tab !== "stats") return; // rebuilding charts on every poll is wasteful when hidden
  // rebuild the 5 charts only when the data changed; round the live "today"
  // minutes so a running session doesn't recreate all charts every 1.5 s poll
  const sig = JSON.stringify({ ...s, minutes: s.minutes && {
    today: Math.round(s.minutes.today), week: s.minutes.week,
    month: s.minutes.month, all: s.minutes.all } });
  if (sig === renderStats._sig) return;
  renderStats._sig = sig;
  chartDefaults();
  const groupLabel = T("gui.stats.group." + (s.group || "day"));
  const days = s.days || 14;
  // grouped labels: day -> MM-DD, week -> YYYY-Www, month -> YYYY-MM
  const lbl = (d) => ((s.group === "day") ? d.date.slice(5) : d.date);
  $("#stat-today").textContent = Math.round(s.minutes.today) + " min";
  $("#stat-week").textContent = Math.round(s.minutes.week) + " min";
  $("#stat-month").textContent = Math.round(s.minutes.month) + " min";
  $("#stat-streak").textContent = s.streak_days + " " + T("gui.day_unit");
  $("#stat-progress").textContent = (s.progress ? s.progress.pct_weighted : 0) + "%";
  $("#chart-time-title").textContent = T("gui.chart.time_title", { group: groupLabel, n: days });
  $("#chart-done-title").textContent = T("gui.chart.done_title", { group: groupLabel, n: days });
  $("#chart-projects-title").textContent = T("gui.chart.by_project", { n: days });
  makeBar("chart-time", s.time_by_day.map((d) => lbl(d.date)),
          s.time_by_day.map((d) => d.minutes), T("gui.chart.minutes"), "#7c5cff");
  makeBar("chart-done", s.tasks_done_by_day.map((d) => lbl(d.date)),
          s.tasks_done_by_day.map((d) => d.count), T("gui.chart.tasks"), "#3fb27f");
  makeDoughnut("chart-tags", s.by_tag.map((t) => t.tag), s.by_tag.map((t) => t.tasks));
  const pct = s.progress ? s.progress.pct_weighted : 0;
  makeDoughnut("chart-progress", [T("gui.chart.done_label"), T("gui.chart.rest")], [pct, Math.max(0, 100 - pct)]);
  // per-project breakdown (colors come from each project)
  const bp = s.by_project || [];
  makeBar("chart-projects", bp.map((p) => p.name), bp.map((p) => p.minutes),
          T("gui.chart.minutes"), bp.map((p) => cssColor(p.color)));
}

/* ---------- AI assistant ---------- */

function renderChat() {
  const box = $("#chat-log");
  box.innerHTML = "";
  for (const m of state.chat) {
    const div = document.createElement("div");
    div.className = "chat-msg " + m.role;
    div.textContent = m.content;
    box.appendChild(div);
  }
  box.scrollTop = box.scrollHeight;
}

async function chatSend() {
  const input = $("#chat-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  state.chat.push({ role: "user", content: q });
  renderChat();
  try {
    const r = await api.post("/api/ai/ask", { question: q });
    state.chat.push({ role: "assistant", content: r.answer + `\n(${r.provider}/${r.model})` });
  } catch (e) {
    state.chat.push({ role: "assistant", content: "⚠ " + e.message });
  }
  renderChat();
}

async function chatWeek() {
  state.chat.push({ role: "user", content: T("gui.chat.week_user") });
  renderChat();
  try {
    const r = await api.post("/api/ai/summarize", {});
    state.chat.push({ role: "assistant", content: r.summary });
  } catch (e) {
    state.chat.push({ role: "assistant", content: "⚠ " + e.message });
  }
  renderChat();
}

async function chatContext() {
  // local-only preview: shows exactly what a provider would receive
  try {
    const r = await api.get("/api/ai/context");
    state.chat.push({ role: "assistant",
      content: `📋 ${T("ai.context.line", { chars: r.chars, max: r.max_chars, provider: r.provider || "-", cloud: r.cloud })}\n\n${r.context}` });
  } catch (e) {
    state.chat.push({ role: "assistant", content: "⚠ " + e.message });
  }
  renderChat();
}

async function chatPlan(apply) {
  const input = $("#plan-input");
  const title = input.value.trim();
  if (!title) return;
  try {
    const r = await api.post("/api/ai/plan", { title, apply });
    const lines = r.proposal.map((s, i) => `${i + 1}. ${s.title}${s.estimate_min ? ` (~${s.estimate_min}min)` : ""}`).join("\n");
    state.chat.push({
      role: "assistant",
      content: (r.applied
        ? T("gui.plan.applied", { n: r.created_task_ids.length, ids: r.created_task_ids.map((i) => "#" + i).join(", ") })
        : T("gui.plan.proposed", { lines })) ,
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
    renderChat();
    await loadAll();
  } catch (e) {
    state.chat.push({ role: "assistant", content: "⚠ " + e.message });
    renderChat();
  }
}

/* ---------- error surfacing: a failed action must never look like "nothing happened" ---------- */

function showError(msg) {
  const t = $("#toast");
  t.textContent = "⚠ " + msg;
  t.classList.remove("hidden");
  t.classList.remove("ok");
  clearTimeout(showError._h);
  showError._h = setTimeout(() => t.classList.add("hidden"), 6000);
}

// wrap every UI action: API errors (400/500/...) land in the toast
async function runAction(fn) {
  try { await fn(); } catch (e) { showError(e.message); }
}

/* ---------- actions ---------- */

async function addTask() {
  const title = $("#new-task-title").value.trim();
  if (!title) { showError(T("gui.err.title_required")); return; } // never fail silently
  const tags = $("#new-task-tags").value.split(",").map((s) => s.trim()).filter(Boolean);
  const est = parseInt($("#new-task-est").value, 10);
  await api.post("/api/tasks/add", {
    title,
    priority: $("#new-task-priority").value,
    tags,
    estimate_min: Number.isNaN(est) ? null : est,
  });
  $("#new-task-title").value = "";
  $("#new-task-tags").value = "";
  $("#new-task-est").value = "";
  notify(T("gui.toast.task_added", { title }));
  await loadAll();
}

async function startSession() {
  const taskId = $("#session-task").value;
  const kind = $("#session-kind-sel").value;
  let minutes = parseInt($("#session-minutes").value, 10);
  if ($("#session-pomodoro").checked && Number.isNaN(minutes) && kind === "focus") {
    // pomodoro: pre-fill the focus duration from settings (pause stays manual in the GUI)
    minutes = (state.settings && state.settings.general.pomodoro_focus_min) || 25;
  }
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
  $("#f-name").focus();
}
function closeModal() { $("#modal").classList.add("hidden"); restoreFocus(); }

// live-detect an existing project folder: engine + git repo + suggested name
let detectTimer = null;
async function detectFolder() {
  const path = $("#f-path").value.trim();
  const hint = $("#f-detect");
  if (!path) { hint.textContent = ""; return; }
  try {
    const d = await api.get("/api/detect?path=" + encodeURIComponent(path));
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
  $("#e-status").value = p.status;
  $("#e-desc").value = p.description || "";
  $("#e-archived").checked = !!p.archived;
  const delBtn = $("#btn-edit-delete");
  delBtn.textContent = T("gui.edit.delete");
  delBtn.classList.remove("btn-danger-armed");
  delBtn.dataset.armed = "";
  $("#e-delete-warn").classList.add("hidden");
  $("#modal-edit").classList.remove("hidden");
  $("#e-name").focus();
}
function closeEditModal() { $("#modal-edit").classList.add("hidden"); state.editingSlug = null; restoreFocus(); }

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
    status: $("#e-status").value,
    description: $("#e-desc").value,
    archived: $("#e-archived").checked,
  });
  closeEditModal();
  await loadAll();
}

async function deleteEditProject() {
  const ref = state.editingSlug;
  if (!ref) return;
  const delBtn = $("#btn-edit-delete");
  if (delBtn.dataset.armed !== "1") {
    // two-step confirm: first click arms, second click deletes
    delBtn.dataset.armed = "1";
    delBtn.textContent = T("gui.edit.delete_confirm");
    delBtn.classList.add("btn-danger-armed");
    $("#e-delete-warn").classList.remove("hidden");
    setTimeout(() => { delBtn.dataset.armed = ""; delBtn.textContent = T("gui.edit.delete"); delBtn.classList.remove("btn-danger-armed"); $("#e-delete-warn").classList.add("hidden"); }, 6000);
    return;
  }
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
  $("#tab-tasks").classList.toggle("hidden", tab !== "tasks");
  $("#tab-log").classList.toggle("hidden", tab !== "log");
  $("#tab-git").classList.toggle("hidden", tab !== "git");
  $("#tab-features").classList.toggle("hidden", tab !== "features");
  $("#tab-ai").classList.toggle("hidden", tab !== "ai");
  $("#tab-settings").classList.toggle("hidden", tab !== "settings");
  $("#tab-stats").classList.toggle("hidden", tab !== "stats");
  if (tab === "stats") renderStats(); // charts are only built while the tab is visible
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
$("#btn-cancel-modal").onclick = closeModal;
$("#btn-create-project").onclick = () => runAction(createProject);
$("#btn-cancel-edit").onclick = closeEditModal;
$("#btn-save-edit").onclick = () => runAction(saveEditProject);
$("#btn-edit-delete").onclick = () => runAction(deleteEditProject);
$("#filter-tag").onchange = async (e) => { state.filterTag = e.target.value; await runAction(async () => { await loadTasks(); renderKanban(); renderTagFilter(); }); };
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
  if (btn.dataset.armed !== "1") {
    btn.dataset.armed = "1";
    btn.textContent = T("gui.task.delete_confirm");
    btn.classList.add("btn-danger-armed");
    setTimeout(() => {
      btn.dataset.armed = "";
      btn.textContent = T("gui.task.delete");
      btn.classList.remove("btn-danger-armed");
    }, 6000);
    return;
  }
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
});
$("#btn-task-verify-approve").onclick = () => runAction(async () => {
  const id = state.editingTaskId;
  if (id == null) return;
  // human click = approval: THIS is what marks the task done, not the AI
  await api.post("/api/tasks/status", { id, status: "done" });
  notify(T("gui.toast.task_done", { id }));
  closeTaskModal();
  await loadAll();
});
$("#btn-export").onclick = () => runAction(doExport);
$("#btn-demo").onclick = () => runAction(doDemoSeed);
$("#btn-upd-check").onclick = () => runAction(checkUpdate);
$("#btn-upd-run").onclick = () => runAction(runUpdate);
wireKanbanDnD();
$("#stats-project").onchange = async (e) => { state.statsFilters.project = e.target.value; await runAction(async () => { await loadStats(); renderStats(); }); };
$("#stats-days").onchange = async (e) => { state.statsFilters.days = e.target.value; await runAction(async () => { await loadStats(); renderStats(); }); };
$("#stats-group").onchange = async (e) => { state.statsFilters.group = e.target.value; await runAction(async () => { await loadStats(); renderStats(); }); };
$("#btn-session-start").onclick = () => runAction(startSession);
$("#btn-session-stop").onclick = () => runAction(stopSession);
$("#session-note").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(stopSession); });
$("#btn-add-log").onclick = () => runAction(addLog);
$("#log-content").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(addLog); });
let logSearchTimer = null;
$("#log-search").addEventListener("input", (e) => {
  state.logQuery = e.target.value;
  clearTimeout(logSearchTimer);
  logSearchTimer = setTimeout(() => runAction(async () => { await loadLogs(); renderLogs(); }), 250);
});
document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
$("#btn-git-link").onclick = () => runAction(gitLink);
$("#btn-git-sync").onclick = () => runAction(gitSync);
$("#btn-git-ignore").onclick = () => runAction(gitIgnore);
$("#btn-git-unlink").onclick = () => runAction(async () => { await api.post("/api/git/unlink", {}); await loadAll(); });
$("#git-unparsed").onchange = async (e) => { state.gitUnparsed = e.target.checked; await runAction(async () => { await loadGit(); renderGit(); }); };
$("#btn-add-feature").onclick = () => runAction(addFeature);
$("#feature-name").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(addFeature); });
$("#btn-chat-send").onclick = () => runAction(chatSend);
$("#chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(chatSend); });
$("#btn-chat-week").onclick = () => runAction(chatWeek);
$("#btn-chat-context").onclick = () => runAction(chatContext);
$("#cfg-provider").onchange = () => { updateProviderFields(); state.settingsDirty = true; };
document.querySelectorAll("#tab-settings input, #tab-settings select, #tab-settings textarea").forEach((el) =>
  el.addEventListener("input", () => { state.settingsDirty = true; }));
$("#btn-ai-save").onclick = () => runAction(saveAllSettings);
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
      return api.get("/api/status").then((s) => { state.lastEventId = s.last_event_id || 0; });
    });
  });
// adaptive polling: fast while visible, slow while the tab/window is hidden
// (the connection dot still updates, but we stop hammering the API)
const POLL_FAST_MS = 1500, POLL_HIDDEN_MS = 10000;
async function pollLoop() {
  await pollEvents();
  setTimeout(pollLoop, document.hidden ? POLL_HIDDEN_MS : POLL_FAST_MS);
}
pollLoop();

/* ---------- utils ---------- */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- UX polish: toasts, relative time, persisted UI, palette, shortcuts ---------- */

// success toast (green); errors keep going through showError (red)
function notify(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  t.classList.add("ok");
  clearTimeout(notify._h);
  notify._h = setTimeout(() => { t.classList.add("hidden"); t.classList.remove("ok"); }, 3500);
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
      statsFilters: state.statsFilters,
    }));
  } catch { /* storage unavailable (private mode): non-fatal */ }
}
function loadUi() {
  try {
    const u = JSON.parse(localStorage.getItem(UI_KEY) || "null");
    if (!u) return;
    if (typeof u.tab === "string") state.tab = u.tab;
    for (const k of ["filterTag", "filterPrio", "taskQuery", "logQuery", "gitTaskFilter"]) {
      if (typeof u[k] === "string") state[k] = u[k];
    }
    if (typeof u.gitUnparsed === "boolean") state.gitUnparsed = u.gitUnparsed;
    if (u.statsFilters && typeof u.statsFilters === "object")
      state.statsFilters = { ...state.statsFilters, ...u.statsFilters };
    $("#filter-search").value = state.taskQuery;
    $("#filter-priority").value = state.filterPrio;
    $("#log-search").value = state.logQuery;
    $("#git-unparsed").checked = state.gitUnparsed;
  } catch { /* corrupted entry: start fresh */ }
}

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
  const items = [];
  for (const c of paletteCommands()) {
    if (match(c.label)) items.push({ sectionKey: "cmd", section: "gui.palette.cmd", label: c.label, run: c.run });
  }
  for (const t of state.tasks.filter((x) => x.status !== "done" && x.status !== "cancelled").slice(0, 30)) {
    const label = T("gui.cmd.open_task", { id: t.id, title: t.title });
    if (match(label)) items.push({ sectionKey: "tasks", section: "gui.palette.tasks", label, run: () => { switchTab("tasks"); openTaskModal(t); } });
  }
  for (const e of (state.logs || []).slice(0, 10)) {
    const label = e.content.slice(0, 80);
    if (match(label)) items.push({ sectionKey: "logs", section: "gui.palette.logs", label, run: () => {
      switchTab("log");
      state.logQuery = e.content.slice(0, 30);
      $("#log-search").value = state.logQuery;
      runAction(async () => { await loadLogs(); renderLogs(); });
    } });
  }
  for (const f of state.features || []) {
    const label = T("gui.cmd.open_feat", { name: f.name });
    if (match(label)) items.push({ sectionKey: "feats", section: "gui.palette.feats", label, run: () => {
      switchTab("features");
      state.featOpen = f.name;
      runAction(async () => { await loadFeatures(); renderFeatures(); });
    } });
  }
  for (const p of state.projects.filter((x) => !x.archived)) { // archived projects are not activatable
    const label = T("gui.cmd.set_active", { name: p.name });
    if (match(label)) items.push({ sectionKey: "projs", section: "gui.palette.projs", label, run: () => runAction(async () => {
      await api.post("/api/projects/set-active", { ref: p.slug });
      notify(T("gui.toast.project_active", { name: p.name }));
      await loadAll();
    }) });
  }
  return items.slice(0, 40);
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
function closePalette() { palette.open = false; $("#palette").classList.add("hidden"); restoreFocus(); }

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
function closeHelp() { $("#modal-help").classList.add("hidden"); restoreFocus(); }
$("#btn-close-help").onclick = closeHelp;

/* ----- global keyboard shortcuts (ignored while typing in a field) ----- */
// built from the VISIBLE tabs so the numbers stay consecutive when the AI tab is hidden
function tabOrder() {
  return ["tasks", "log", "git", "features", "stats", "ai", "settings"]
    .filter((t) => t !== "ai" || state.ai.enabled);
}
document.addEventListener("keydown", (e) => {
  if (paletteKey(e)) return;
  if (modalTrapTab(e)) return; // keep Tab inside the open modal/palette
  const typing = e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" ||
                             e.target.tagName === "SELECT" || e.target.isContentEditable);
  if (e.key === "Escape") {
    // close the topmost open layer
    if (!$("#modal-task").classList.contains("hidden")) return closeTaskModal();
    if (!$("#modal-edit").classList.contains("hidden")) return closeEditModal();
    if (!$("#modal").classList.contains("hidden")) return closeModal();
    if (!$("#modal-help").classList.contains("hidden")) return closeHelp();
    return;
  }
  // while a modal is open, single-key shortcuts stay out of the way (except Esc above)
  const modalOpen = ["#modal", "#modal-edit", "#modal-task", "#modal-help"]
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
    case "1": case "2": case "3": case "4": case "5": case "6": case "7": {
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
