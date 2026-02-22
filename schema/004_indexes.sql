-- =============================================================================
-- 004_indexes.sql — Performance indexes
-- Run after all tables exist
-- =============================================================================

-- memory_log — most frequently queried table
CREATE INDEX IF NOT EXISTS idx_memory_log_recorded_at   ON memory_log (recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_log_event_time    ON memory_log (event_time DESC);
CREATE INDEX IF NOT EXISTS idx_memory_log_agent_id      ON memory_log (agent_id);
CREATE INDEX IF NOT EXISTS idx_memory_log_source_id     ON memory_log (source_id);
CREATE INDEX IF NOT EXISTS idx_memory_log_session_id    ON memory_log (session_id);
CREATE INDEX IF NOT EXISTS idx_memory_log_event_type    ON memory_log (event_type);
CREATE INDEX IF NOT EXISTS idx_memory_log_importance    ON memory_log (importance);
CREATE INDEX IF NOT EXISTS idx_memory_log_processed     ON memory_log (processed) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_memory_log_tags          ON memory_log USING gin(tags);

-- Full-text search on content
CREATE INDEX IF NOT EXISTS idx_memory_log_content_trgm
    ON memory_log USING gin(content gin_trgm_ops);

-- Full-text search using tsvector
ALTER TABLE memory_log ADD COLUMN IF NOT EXISTS content_tsv TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX IF NOT EXISTS idx_memory_log_content_tsv
    ON memory_log USING gin(content_tsv);

-- JSONB payload index for flexible queries
CREATE INDEX IF NOT EXISTS idx_memory_log_payload
    ON memory_log USING gin(payload);

-- sessions
CREATE INDEX IF NOT EXISTS idx_sessions_agent_id    ON sessions (agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at  ON sessions (started_at DESC);

-- gists
CREATE INDEX IF NOT EXISTS idx_gists_period         ON gists (period_start DESC, period_end DESC);
CREATE INDEX IF NOT EXISTS idx_gists_type           ON gists (gist_type);
CREATE INDEX IF NOT EXISTS idx_gists_tags           ON gists USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_gists_content_trgm   ON gists USING gin(content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_gists_obsidian_path  ON gists (obsidian_path);

-- obsidian_notes
CREATE INDEX IF NOT EXISTS idx_obsidian_notes_type      ON obsidian_notes (note_type);
CREATE INDEX IF NOT EXISTS idx_obsidian_notes_tags      ON obsidian_notes USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_obsidian_notes_updated   ON obsidian_notes (updated_at DESC);

-- alert_history
CREATE INDEX IF NOT EXISTS idx_alert_history_fired_at   ON alert_history (fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_history_rule_id    ON alert_history (rule_id);

-- ---------------------------------------------------------------------------
-- Useful views
-- ---------------------------------------------------------------------------

-- Recent unprocessed events for the analysis worker
CREATE OR REPLACE VIEW v_pending_analysis AS
    SELECT ml.*, a.name AS agent_name, s.name AS source_name
    FROM memory_log ml
    LEFT JOIN agents a  ON a.id = ml.agent_id
    LEFT JOIN sources s ON s.id = ml.source_id
    WHERE ml.processed = FALSE
    ORDER BY ml.recorded_at ASC;

-- Daily event summary
CREATE OR REPLACE VIEW v_daily_summary AS
    SELECT
        DATE(event_time)        AS day,
        source_id,
        s.name                  AS source_name,
        event_type,
        COUNT(*)                AS count,
        MAX(importance)         AS max_importance,
        AVG(importance)::NUMERIC(3,1) AS avg_importance
    FROM memory_log ml
    JOIN sources s ON s.id = ml.source_id
    GROUP BY 1, 2, 3, 4
    ORDER BY 1 DESC, 5 DESC;

-- Last 100 events with agent/source names
CREATE OR REPLACE VIEW v_recent_events AS
    SELECT
        ml.id,
        ml.event_time,
        ml.event_type,
        ml.importance,
        a.name  AS agent_name,
        s.name  AS source_name,
        ml.content,
        ml.tags,
        ml.processed
    FROM memory_log ml
    LEFT JOIN agents a  ON a.id = ml.agent_id
    LEFT JOIN sources s ON s.id = ml.source_id
    ORDER BY ml.event_time DESC
    LIMIT 100;
