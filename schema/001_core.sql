-- =============================================================================
-- 001_core.sql — Episodic Memory (Layer 1)
-- Append-only log: no UPDATE/DELETE ever allowed on memory_log
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- full-text trigram search

-- ---------------------------------------------------------------------------
-- Agents registry — all AI agents that write/read memory
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL UNIQUE,          -- 'claude', 'codex', 'gemini', 'openclaw'
    description TEXT,
    api_key_ref TEXT,                          -- reference label (not the key itself)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata    JSONB NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------------
-- Sources registry — all data sources that push events
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL UNIQUE,          -- 'haos', 'telegram', 'twitter', 'github', etc.
    source_type TEXT NOT NULL,                 -- 'agent', 'device', 'social', 'import', 'webhook'
    description TEXT,
    config      JSONB NOT NULL DEFAULT '{}',   -- source-specific config (webhook url, poll interval)
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Sessions — a session groups related memory_log entries
-- (e.g. one conversation, one analysis run, one HAOS event burst)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id    UUID REFERENCES agents(id),
    source_id   UUID REFERENCES sources(id),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at    TIMESTAMPTZ,
    context     JSONB NOT NULL DEFAULT '{}',   -- session metadata (task description, etc.)
    tags        TEXT[] NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------------
-- memory_log — THE EPISODIC STORE
-- Append-only: every observation, action, event, message ever recorded
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_log (
    id              BIGSERIAL PRIMARY KEY,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_time      TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- when it actually happened
    session_id      UUID REFERENCES sessions(id),
    agent_id        UUID REFERENCES agents(id),
    source_id       UUID REFERENCES sources(id),
    event_type      TEXT NOT NULL,     -- 'message', 'action', 'observation', 'haos_event',
                                       -- 'social_post', 'import', 'alert', 'system'
    content         TEXT NOT NULL,     -- raw text representation
    payload         JSONB NOT NULL DEFAULT '{}',  -- structured data
    importance      SMALLINT NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
                                       -- 1=trivial, 3=normal, 5=critical
    tags            TEXT[] NOT NULL DEFAULT '{}',
    processed       BOOLEAN NOT NULL DEFAULT FALSE,  -- has analysis engine seen this?
    gist_id         BIGINT             -- set after semantic processing (FK added later)
);

-- ---------------------------------------------------------------------------
-- Append-only enforcement: block UPDATE and DELETE on memory_log
-- ---------------------------------------------------------------------------
CREATE OR REPLACE RULE memory_log_no_update AS
    ON UPDATE TO memory_log DO INSTEAD NOTHING;

CREATE OR REPLACE RULE memory_log_no_delete AS
    ON DELETE TO memory_log DO INSTEAD NOTHING;

-- ---------------------------------------------------------------------------
-- Seed core agents and sources
-- ---------------------------------------------------------------------------
INSERT INTO agents (name, description) VALUES
    ('claude',   'Anthropic Claude — code and analysis agent'),
    ('codex',    'OpenAI Codex / GPT series — code agent'),
    ('gemini',   'Google Gemini — multimodal agent'),
    ('openclaw', 'OpenClaw — personal AI assistant with social channel integrations')
ON CONFLICT (name) DO NOTHING;

INSERT INTO sources (name, source_type, description) VALUES
    ('claude_api',     'agent',   'Anthropic Claude API conversations'),
    ('codex_api',      'agent',   'OpenAI API conversations'),
    ('gemini_api',     'agent',   'Google Gemini API conversations'),
    ('openclaw',       'agent',   'OpenClaw agent events and heartbeat'),
    ('haos',           'device',  'Home Assistant OS — home automation events'),
    ('telegram',       'social',  'Telegram messages and bot interactions'),
    ('whatsapp',       'social',  'WhatsApp messages via OpenClaw'),
    ('imessage',       'social',  'iMessage via OpenClaw'),
    ('twitter',        'social',  'Twitter/X posts and DMs'),
    ('github',         'social',  'GitHub events, PRs, issues, commits'),
    ('file_import',    'import',  'Bulk data import from exported account data'),
    ('system',         'system',  'System events: backup, sync, startup, errors')
ON CONFLICT (name) DO NOTHING;
