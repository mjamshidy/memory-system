-- =============================================================================
-- 002_semantic.sql — Semantic Memory (Layer 2)
-- Gists, extracted facts, Obsidian note tracking, concept links
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Gists — the semantic "gist" of one or more episodic entries
-- Created by the analysis engine, referenced from memory_log.gist_id
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gists (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period_start    TIMESTAMPTZ NOT NULL,      -- temporal span covered
    period_end      TIMESTAMPTZ NOT NULL,
    source_ids      UUID[] NOT NULL DEFAULT '{}',
    agent_ids       UUID[] NOT NULL DEFAULT '{}',
    log_ids         BIGINT[] NOT NULL DEFAULT '{}',  -- memory_log entries this gist covers
    gist_type       TEXT NOT NULL,             -- 'fact', 'summary', 'pattern', 'decision',
                                               -- 'event_analysis', 'digest', 'query_result'
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,             -- the gist itself (markdown)
    tags            TEXT[] NOT NULL DEFAULT '{}',
    importance      SMALLINT NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    llm_model       TEXT,                      -- which LLM generated this
    obsidian_path   TEXT,                      -- relative path in vault if written
    metadata        JSONB NOT NULL DEFAULT '{}'
);

-- Add FK from memory_log to gists now that gists table exists
ALTER TABLE memory_log
    ADD CONSTRAINT memory_log_gist_fk
    FOREIGN KEY (gist_id) REFERENCES gists(id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

-- ---------------------------------------------------------------------------
-- Obsidian notes — track every note written to the vault
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS obsidian_notes (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    vault_path      TEXT NOT NULL UNIQUE,      -- e.g. 'Memory/Events/2026-02-21/haos-motion.md'
    note_type       TEXT NOT NULL,             -- 'event_card', 'analysis', 'digest', 'query', 'alert'
    title           TEXT NOT NULL,
    gist_id         BIGINT REFERENCES gists(id),
    tags            TEXT[] NOT NULL DEFAULT '{}',
    linked_notes    TEXT[] NOT NULL DEFAULT '{}',  -- vault_paths of linked notes
    checksum        TEXT                       -- sha256 of content (detect external edits)
);

-- ---------------------------------------------------------------------------
-- Concept nodes — the graph nodes in the semantic layer
-- Obsidian handles the visual graph; this mirrors it in postgres for querying
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS concepts (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,          -- 'Home Security', 'Health', 'Finance', etc.
    description TEXT,
    tags        TEXT[] NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Concept links — edges between concepts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS concept_links (
    id              BIGSERIAL PRIMARY KEY,
    from_concept_id BIGINT NOT NULL REFERENCES concepts(id),
    to_concept_id   BIGINT NOT NULL REFERENCES concepts(id),
    link_type       TEXT NOT NULL DEFAULT 'related',  -- 'related', 'causes', 'part_of', 'example_of'
    weight          FLOAT NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(from_concept_id, to_concept_id, link_type)
);

-- ---------------------------------------------------------------------------
-- Note–concept associations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS note_concepts (
    note_id     BIGINT NOT NULL REFERENCES obsidian_notes(id),
    concept_id  BIGINT NOT NULL REFERENCES concepts(id),
    relevance   FLOAT NOT NULL DEFAULT 1.0,
    PRIMARY KEY (note_id, concept_id)
);

-- ---------------------------------------------------------------------------
-- Seed core concept nodes
-- ---------------------------------------------------------------------------
INSERT INTO concepts (name, description) VALUES
    ('Home',          'Home automation, devices, security'),
    ('Health',        'Health data, medical, fitness'),
    ('Finance',       'Financial events, transactions, tax'),
    ('Social',        'Social media, communications'),
    ('Work',          'Work projects, code, tasks'),
    ('Family',        'Family events and communications'),
    ('AI Agents',     'Interactions with AI agents'),
    ('System',        'System events, backups, errors')
ON CONFLICT (name) DO NOTHING;
