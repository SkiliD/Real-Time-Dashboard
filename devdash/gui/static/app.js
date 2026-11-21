/* DevDash GUI frontend (steps c-e: projects, tasks, sessions/timer, devlog).
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
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.statusText);
      return d;
    }),
};

const state = {
  projects: [],
  active: null, // slug
  tasks: [],
  session: null,
  logs: [],
  logQuery: "",
  filterTag: "",
  lastEventId: 0,
  tab: "tasks",
  gitStatus: null,
  gitCommits: [],
  gitUnparsed: false,
  features: [],
  ai: { enabled: false },
  chat: [],
  stats: null,
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

async function loadAll() {
  const p = await api.get("/api/projects");
  state.projects = p.projects;
  state.active = p.active;
  // all panel loads are independent: run them concurrently instead of 9 sequential round-trips
  await Promise.all([
    loadTasks(), loadSession(), loadSessions(), loadLogs(), loadGit(),
    loadFeatures(), loadAiStatus(), loadStats(),
  ]);
  state.status = await api.get("/api/status");
  render();
}

async function loadTasks() {
  const q = new URLSearchParams({ all: "1" });
  if (state.filterTag) q.set("tag", state.filterTag);
  state.tasks = (await api.get("/api/tasks?" + q.toString())).tasks;
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
    state.gitCommits = (await api.get("/api/git/commits?" + q.toString())).commits;
  } catch {
    state.gitStatus = { linked: false };
    state.gitCommits = [];
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
    state.stats = await api.get("/api/stats?days=14");
  } catch {
    state.stats = null;
  }
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

/* ---------- rendering ---------- */

function render() {
  renderProjects();
  renderHeader();
  renderKanban();
  renderTagFilter();
  renderSessionPanel();
  renderSessions();
  renderLogs();
  renderGit();
  renderFeatures();
  renderChat();
  renderStats();
}

// project colors end up in a style attribute: only accept #rrggbb (the API
// validates on write too — this is defense in depth against a hand-edited DB)
function cssColor(c) {
  return /^#[0-9a-fA-F]{6}$/.test(c || "") ? c : "#8a8f98";
}

function renderProjects() {
  const ul = $("#project-list");
  ul.innerHTML = "";
  for (const p of state.projects) {
    const li = document.createElement("li");
    if (p.slug === state.active) li.classList.add("active");
    li.innerHTML =
      `<span class="dot" style="background:${cssColor(p.color)}"></span>` +
      `<span>${escapeHtml(p.name)}</span>` +
      `<span class="meta">${T("engine." + p.engine)}</span>`;
    li.title = p.slug + " — " + T("pstatus." + p.status);
    li.onclick = () => runAction(async () => {
      await api.post("/api/projects/set-active", { ref: p.slug });
      await loadAll();
    });
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
    `<h1>${escapeHtml(p.name)}</h1>` +
    `<span class="sub">${T("gui.header.sub", { engine: T("engine." + p.engine), status: T("pstatus." + p.status), open })}${prog}${today}${streak}</span>`;
  const sel = $("#session-task");
  const inProgress = state.tasks.filter((t) => t.status === "in_progress");
  const current = sel.value;
  sel.innerHTML = `<option value="">${T("gui.no_task")}</option>` +
    inProgress.map((t) => `<option value="${t.id}">#${t.id} ${escapeHtml(t.title)}</option>`).join("");
  if (inProgress.some((t) => String(t.id) === current)) sel.value = current;
}

function renderKanban() {
  for (const status of ["todo", "in_progress", "done"]) {
    const col = $("#col-" + status);
    const list = state.tasks.filter((t) => t.status === status);
    $("#count-" + status).textContent = list.length;
    col.innerHTML = "";
    for (const t of list) col.appendChild(taskCard(t));
  }
}

function taskCard(t) {
  const card = document.createElement("div");
  card.className = "card " + t.status;
  const tags = t.tags.map((x) => `<span class="tag">${escapeHtml(x)}</span>`).join("");
  const est = t.estimate_min ? `<span class="est">${T("gui.est", { n: t.estimate_min })}</span>` : "";
  const actions = [];
  if (t.status === "todo") actions.push(`<button class="btn btn-sm" data-act="in_progress">${T("gui.task.start")}</button>`);
  if (t.status === "in_progress") {
    actions.push(`<button class="btn btn-sm" data-act="done">${T("gui.task.done")}</button>`);
    actions.push(`<button class="btn btn-sm" data-act="todo">${T("gui.task.todo")}</button>`);
  }
  if (t.status === "done") actions.push(`<button class="btn btn-sm" data-act="todo">${T("gui.task.reopen")}</button>`);
  if (t.status !== "cancelled" && t.status !== "done") actions.push(`<button class="btn btn-sm" data-act="cancelled">${T("gui.task.cancel")}</button>`);
  card.innerHTML =
    `<div class="title">#${t.id} ${escapeHtml(t.title)}</div>` +
    `<div class="row"><span class="prio prio-${t.priority}">${T("prio." + t.priority)}</span>${tags}${est}</div>` +
    `<div class="actions">${actions.join("")}</div>`;
  card.querySelectorAll("button[data-act]").forEach((b) => {
    b.onclick = () => runAction(async () => {
      await api.post("/api/tasks/status", { id: t.id, status: b.dataset.act });
      await loadAll();
    });
  });
  return card;
}

function renderTagFilter() {
  const sel = $("#filter-tag");
  const tags = [...new Set(state.tasks.flatMap((t) => t.tags))].sort();
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
      `<span class="mono dim">${s.started_at.slice(0, 16).replace("T", " ")}</span> · ` +
      `<span>${T("kind." + s.kind)}</span> · <b>${dur}</b>` +
      (s.task_id ? ` · <span class="dim">#${s.task_id}</span>` : "") +
      (s.note ? ` — <span class="dim">${escapeHtml(s.note)}</span>` : "");
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = `<li class="dim">${T("gui.sessions.empty")}</li>`;
}

/* ---------- devlog (step e) ---------- */

function renderLogs() {
  const ul = $("#log-list");
  ul.innerHTML = "";
  for (const e of state.logs) {
    const li = document.createElement("li");
    li.className = "log-entry";
    const tags = e.tags.map((x) => `<span class="tag">${escapeHtml(x)}</span>`).join("");
    li.innerHTML =
      `<div class="log-head"><span class="mono dim">${e.created_at.slice(0, 16).replace("T", " ")}</span>` +
      `${tags}${e.task_id ? ` <span class="tag">#${e.task_id}</span>` : ""}</div>` +
      `<div class="log-content">${escapeHtml(e.content)}</div>`;
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
  await loadAll();
}

/* ---------- git (step f) ---------- */

function renderGit() {
  const st = state.gitStatus || {};
  const box = $("#git-status");
  if (!st.linked) {
    box.textContent = T("gui.git.none");
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
      `<span class="dim">${escapeHtml(c.committed_at.slice(0, 16).replace("T", " "))}</span> ` +
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
  await api.post("/api/git/sync", {});
  await loadAll();
}

/* ---------- features (step h) ---------- */

function renderFeatures() {
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
      `<div class="feat-head"><b>⚑ ${escapeHtml(f.name)}</b> ` +
      `<span class="tag">${T("fstatus." + f.status)}</span> ` +
      (f.source === "ai" && f.confidence != null ? `<span class="dim">${T("gui.feat.confidence", { pct: (f.confidence * 100).toFixed(0) })}</span>` : "") +
      `</div>` +
      `<div class="feat-progress"><div class="bar" style="width:${bar}%"></div>` +
      `<span class="dim">${T("gui.feat.progress", { done: p.done, total: p.total, pct: p.pct })}</span></div>` +
      `<div class="feat-actions">${actions}</div>`;
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
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = `<li class="dim">${T("gui.feat.empty")}</li>`;
}

async function addFeature() {
  const name = $("#feature-name").value.trim();
  if (!name) return;
  await api.post("/api/features/add", { name });
  $("#feature-name").value = "";
  await loadAll();
}

/* ---------- stats & charts (step j, Chart.js embarqué) ---------- */

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
    data: { labels, datasets: [{ label, data, backgroundColor: color, borderRadius: 4 }] },
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
  if (!s || !s.project) return;
  if (state.tab !== "stats") return; // rebuilding 4 charts on every poll is wasteful when hidden
  chartDefaults();
  $("#stat-today").textContent = Math.round(s.minutes.today) + " min";
  $("#stat-week").textContent = Math.round(s.minutes.week) + " min";
  $("#stat-month").textContent = Math.round(s.minutes.month) + " min";
  $("#stat-streak").textContent = s.streak_days + " " + T("gui.day_unit");
  $("#stat-progress").textContent = (s.progress ? s.progress.pct_weighted : 0) + "%";
  makeBar("chart-time", s.time_by_day.map((d) => d.date.slice(5)),
          s.time_by_day.map((d) => d.minutes), T("gui.chart.minutes"), "#7c5cff");
  makeBar("chart-done", s.tasks_done_by_day.map((d) => d.date.slice(5)),
          s.tasks_done_by_day.map((d) => d.count), T("gui.chart.tasks"), "#3fb27f");
  makeDoughnut("chart-tags", s.by_tag.map((t) => t.tag), s.by_tag.map((t) => t.tasks));
  const pct = s.progress ? s.progress.pct_weighted : 0;
  makeDoughnut("chart-progress", [T("gui.chart.done_label"), T("gui.chart.rest")], [pct, Math.max(0, 100 - pct)]);
}

/* ---------- AI assistant (step i, optional) ---------- */

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
    }
    if (r.applied) input.value = "";
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
  if (!title) return;
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
  await loadAll();
}

async function startSession() {
  const taskId = $("#session-task").value;
  await api.post("/api/session/start", { kind: "focus", task_id: taskId ? parseInt(taskId, 10) : null });
  await loadAll();
}

async function stopSession() {
  const note = $("#session-note").value.trim();
  await api.post("/api/session/stop", { note });
  $("#session-note").value = "";
  await loadAll();
}

/* ---------- modal (new project) ---------- */

function openModal() { $("#modal").classList.remove("hidden"); $("#f-name").focus(); }
function closeModal() { $("#modal").classList.add("hidden"); }

async function createProject() {
  const name = $("#f-name").value.trim();
  if (!name) return;
  await api.post("/api/projects", {
    name,
    engine: $("#f-engine").value,
    color: $("#f-color").value,
    status: $("#f-status").value,
    description: $("#f-desc").value,
    set_active: true,
  });
  closeModal();
  await loadAll();
}

/* ---------- tabs ---------- */

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $("#tab-tasks").classList.toggle("hidden", tab !== "tasks");
  $("#tab-log").classList.toggle("hidden", tab !== "log");
  $("#tab-git").classList.toggle("hidden", tab !== "git");
  $("#tab-features").classList.toggle("hidden", tab !== "features");
  $("#tab-ai").classList.toggle("hidden", tab !== "ai");
  $("#tab-stats").classList.toggle("hidden", tab !== "stats");
  if (tab === "stats") renderStats(); // charts are only built while the tab is visible
}

/* ---------- wiring ---------- */

$("#btn-add-task").onclick = () => runAction(addTask);
$("#new-task-title").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(addTask); });
$("#btn-new-project").onclick = openModal;
$("#btn-cancel-modal").onclick = closeModal;
$("#btn-create-project").onclick = () => runAction(createProject);
$("#filter-tag").onchange = async (e) => { state.filterTag = e.target.value; await runAction(async () => { await loadTasks(); renderKanban(); }); };
$("#btn-session-start").onclick = () => runAction(startSession);
$("#btn-session-stop").onclick = () => runAction(stopSession);
$("#session-note").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(stopSession); });
$("#btn-add-log").onclick = () => runAction(addLog);
$("#log-content").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(addLog); });
$("#log-search").addEventListener("input", async (e) => {
  state.logQuery = e.target.value;
  await runAction(async () => { await loadLogs(); renderLogs(); });
});
document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
$("#btn-git-link").onclick = () => runAction(gitLink);
$("#btn-git-sync").onclick = () => runAction(gitSync);
$("#btn-git-unlink").onclick = () => runAction(async () => { await api.post("/api/git/unlink", {}); await loadAll(); });
$("#git-unparsed").onchange = async (e) => { state.gitUnparsed = e.target.checked; await runAction(async () => { await loadGit(); renderGit(); }); };
$("#btn-add-feature").onclick = () => runAction(addFeature);
$("#feature-name").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(addFeature); });
$("#btn-chat-send").onclick = () => runAction(chatSend);
$("#chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter") runAction(chatSend); });
$("#btn-chat-week").onclick = () => runAction(chatWeek);
$("#btn-chat-context").onclick = () => runAction(chatContext);
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
  .finally(() => loadAll().then(() => api.get("/api/status").then((s) => { state.lastEventId = s.last_event_id || 0; })));
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
