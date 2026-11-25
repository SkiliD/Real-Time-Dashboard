-- DevDash database schema (SQLite, single file devdash.db)
-- All timestamps are ISO-8601 UTC strings. Tags are JSON arrays stored as TEXT.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,          -- url-friendly unique name
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    engine      TEXT NOT NULL DEFAULT 'other', -- unity|unreal|godot|web|other (free label)
    color       TEXT NOT NULL DEFAULT '#8a8f98', -- identity color (hex)
    languages   TEXT NOT NULL DEFAULT '[]',    -- JSON array: programming languages used (csharp, gdscript, ...)
    path        TEXT NOT NULL DEFAULT '',      -- connected project folder (empty = none, set via `projects connect`)
    status      TEXT NOT NULL DEFAULT 'idea',  -- idea|design|prototype|development|beta|done|paused
    archived    INTEGER NOT NULL DEFAULT 0,   -- soft hide, never hard delete
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS features (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open',  -- open|proposed|done|rejected
    source       TEXT NOT NULL DEFAULT 'manual',-- manual|ai (who proposed validation)
    confidence   REAL,                          -- AI verdict confidence (0..1), NULL if manual
    reasoning    TEXT,                          -- AI verdict reasoning, NULL if manual
    feedback     TEXT,                          -- user feedback on a rejected proposal
    created_at   TEXT NOT NULL,
    validated_at TEXT,                          -- set ONLY by a human decision
    deleted_at   TEXT                           -- soft delete (corbeille); NULL = visible
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_id    INTEGER REFERENCES tasks(id) ON DELETE CASCADE, -- subtasks
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    priority     TEXT NOT NULL DEFAULT 'medium', -- critical|high|medium|low
    estimate_min INTEGER,                        -- optional time estimate
    status       TEXT NOT NULL DEFAULT 'todo',   -- todo|in_progress|done|cancelled
    tags         TEXT NOT NULL DEFAULT '[]',     -- JSON array of free tags
    feature_id   INTEGER REFERENCES features(id) ON DELETE SET NULL,
    order_index  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    completed_at TEXT,
    deleted_at   TEXT                           -- soft delete (corbeille); NULL = visible
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_feature ON tasks(feature_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent  ON tasks(parent_id);
-- v11: the poll-path list query sorts by CASE status/priority, order_index, id —
-- an expression index matching that ORDER BY avoids a full temp-b-tree sort per call
CREATE INDEX IF NOT EXISTS idx_tasks_sort ON tasks(
    project_id,
    CASE status WHEN 'in_progress' THEN 0 WHEN 'todo' THEN 1 WHEN 'done' THEN 2 ELSE 3 END,
    CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
    order_index, id
) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id      INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL DEFAULT 'focus', -- focus|pause|custom (pomodoro cycle)
    started_at   TEXT NOT NULL,
    ended_at     TEXT,                          -- NULL while running
    duration_min REAL,                          -- filled on stop
    planned_min  INTEGER,                       -- optional target duration (pomodoro focus/break)
    note         TEXT NOT NULL DEFAULT ''       -- "what I did / what blocks me"
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_task    ON sessions(task_id);
CREATE INDEX IF NOT EXISTS idx_sessions_project_started ON sessions(project_id, started_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_project_task ON sessions(project_id, task_id);
-- partial index: the running-session lookup (ended_at IS NULL) is O(1) instead of a full scan
CREATE INDEX IF NOT EXISTS idx_sessions_running ON sessions(id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS log_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id    INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    content    TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '[]',      -- JSON array
    created_at TEXT NOT NULL,
    deleted_at TEXT                           -- soft delete (corbeille); NULL = visible
);
CREATE INDEX IF NOT EXISTS idx_log_project ON log_entries(project_id);
CREATE INDEX IF NOT EXISTS idx_log_task    ON log_entries(task_id);
CREATE INDEX IF NOT EXISTS idx_log_project_created ON log_entries(project_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS git_links (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    repo_path  TEXT NOT NULL,                   -- local git repo, read-only access
    added_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS git_commits (
    hash           TEXT PRIMARY KEY,
    repo_path      TEXT NOT NULL,
    short          TEXT NOT NULL,
    author         TEXT NOT NULL,
    message        TEXT NOT NULL,
    branch         TEXT,
    committed_at   TEXT NOT NULL,
    parsed_task_id INTEGER,                          -- from "#42" etc. (no FK: task may not exist yet)
    parsed_feature TEXT                              -- from "feat(xyz)" etc.
);
CREATE INDEX IF NOT EXISTS idx_commits_repo  ON git_commits(repo_path);
CREATE INDEX IF NOT EXISTS idx_commits_task  ON git_commits(parsed_task_id);
-- committed_at is normalized UTC ISO (+00:00): lexicographic = chronological
CREATE INDEX IF NOT EXISTS idx_commits_repo_date ON git_commits(repo_path, committed_at);
-- v11: /api/git/commits orders by committed_at DESC, hash DESC per repo — include the
-- hash tiebreak in the index so no temp b-tree sort is needed per poll
CREATE INDEX IF NOT EXISTS idx_commits_repo_date_hash ON git_commits(repo_path, committed_at DESC, hash DESC);
CREATE INDEX IF NOT EXISTS idx_commits_date ON git_commits(committed_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks(completed_at) WHERE status = 'done' AND completed_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS git_sync_state (
    repo_path      TEXT PRIMARY KEY,
    last_sync_at   TEXT,
    last_seen_hash TEXT,
    branch         TEXT   -- cached by the last sync; lets git status be pure-DB (v4)
);

-- Append-only event bus: every core mutation appends a row. The GUI polls
-- `events` (since last seen id) to refresh live; the CLI can `--watch` it.
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity     TEXT NOT NULL,                   -- project|task|feature|session|log|git|config
    entity_id  TEXT,                            -- row id as string (nullable)
    action     TEXT NOT NULL,                   -- created|updated|status|activated|...
    payload    TEXT NOT NULL DEFAULT '{}',      -- JSON
    created_at TEXT NOT NULL
);

-- Persisted AI chat history: one row per message, per project. Only written by
-- /api/ai/ask (user question + assistant answer); cleared via /api/ai/chat/clear.
CREATE TABLE IF NOT EXISTS ai_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    role       TEXT NOT NULL,                -- user | assistant
    content    TEXT NOT NULL,
    meta       TEXT,                         -- "provider / model [/ cloud]"
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_messages_project ON ai_messages(project_id, id);
