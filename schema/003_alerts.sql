-- =============================================================================
-- 003_alerts.sql — Alert rules and history
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Alert rules — declarative rules that trigger notifications
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_rules (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    rule_type       TEXT NOT NULL,     -- 'keyword', 'pattern', 'threshold', 'schedule', 'composite'
    condition       JSONB NOT NULL,    -- rule-specific condition definition
    sources         TEXT[],            -- filter to specific source names (null = all)
    event_types     TEXT[],            -- filter to specific event_type values (null = all)
    importance_min  SMALLINT DEFAULT 1,
    channels        TEXT[] NOT NULL DEFAULT '{telegram}',  -- 'telegram', 'openclaw', 'obsidian'
    cooldown_secs   INTEGER DEFAULT 300,   -- min seconds between repeated fires
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Alert history — every time a rule fires
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_history (
    id              BIGSERIAL PRIMARY KEY,
    fired_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rule_id         BIGINT NOT NULL REFERENCES alert_rules(id),
    memory_log_id   BIGINT REFERENCES memory_log(id),
    message         TEXT NOT NULL,
    channels_sent   TEXT[] NOT NULL DEFAULT '{}',
    success         BOOLEAN NOT NULL DEFAULT TRUE,
    error_detail    TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------------
-- Seed default alert rules
-- ---------------------------------------------------------------------------
INSERT INTO alert_rules (name, description, rule_type, condition, sources, channels, importance_min) VALUES
    (
        'critical_importance',
        'Fire immediately for any importance=5 event',
        'threshold',
        '{"field": "importance", "operator": "gte", "value": 5}',
        NULL,
        '{telegram, obsidian}',
        5
    ),
    (
        'haos_security_alert',
        'Home security events from HAOS',
        'keyword',
        '{"keywords": ["motion", "door", "alarm", "intrusion", "security", "lock"]}',
        '{haos}',
        '{telegram, openclaw}',
        3
    ),
    (
        'system_backup_failure',
        'Alert if backup or sync fails',
        'keyword',
        '{"keywords": ["backup_failed", "sync_failed", "error"]}',
        '{system}',
        '{telegram}',
        4
    ),
    (
        'agent_error',
        'Any agent reports an error',
        'pattern',
        '{"pattern": "(error|exception|failed|crash)", "flags": "i"}',
        '{claude_api, codex_api, gemini_api, openclaw}',
        '{telegram}',
        4
    )
ON CONFLICT (name) DO NOTHING;
