# Memory System — Architecture & Implementation Plan

## System Overview

A two-layer persistent memory infrastructure for AI agents and personal data aggregation.

```
[ALL SOURCES] ──────────────────────────────────────────────────────┐
  Agents: Claude, Codex, Gemini, OpenClaw                           │
  Home:   HAOS / home devices via webhooks                          │
  Social: platform APIs & webhooks                                  │
  Data:   bulk account dumps (CSV/JSON)                             │
          OpenClaw events (heartbeat, skills output)                │
                                    ▼                               │
                    ┌───────────────────────────────┐               │
                    │     INGESTION SERVER          │               │
                    │     FastAPI  :8765            │               │
                    │  normalise → validate → store │               │
                    └──────────────┬────────────────┘               │
                                   ▼                                │
         ┌──────────────────────────────────────────────┐           │
         │         POSTGRESQL 18 (local, Homebrew)      │           │
         │   Layer 1 — EPISODIC (append-only log)       │           │
         │   memory_log | sessions | sources | agents   │           │
         │   Layer 2 stubs — gists | notes | links      │           │
         └──────────┬───────────────────────────────────┘           │
                    │                                                │
          ┌─────────┼──────────────┐                                │
          ▼         ▼              ▼                                 │
   [iCLOUD BACKUP] [SUPABASE]  [ANALYSIS ENGINE]                    │
   ~/Library/…     periodic     event-triggered                     │
   /CloudDocs/     pg_dump+     periodic digests                    │
   DB Backups/     restore      query-on-demand                     │
                                    │                               │
                          ┌─────────┴──────────┐                   │
                          ▼                    ▼                    │
                   [OBSIDIAN VAULT]   [NOTIFICATIONS]               │
                   Liquid graph       Telegram bot                  │
                   Event cards        OpenClaw dispatch             │
                   Analysis reports   (WhatsApp/iMessage)           │
                   Query results                                    │
```

## Key Design Decisions

### 1. Append-only enforcement (episodic integrity)
- PostgreSQL RULE blocks UPDATE/DELETE on `memory_log`
- Application layer never issues mutations
- Only INSERTs allowed; records are never changed, only superseded by new records

### 2. Obsidian as liquid semantic graph
- Notes created dynamically by type: event card, analysis report, query result, digest, alert
- All notes are linked topically (not just by date)
- Tags: `#source/haos`, `#agent/claude`, `#significance/high`, `#type/analysis`
- Folder structure: `Memory/Events/`, `Memory/Analysis/`, `Memory/Queries/`, `Memory/Digests/`

### 3. Configurable LLM
- Factory class supports: Claude (Anthropic), OpenAI (GPT), Google (Gemini), Ollama (local)
- Config drives which model runs each task (gist=claude, digest=gpt4o, alerts=local, etc.)

### 4. OpenClaw integration
- Expose a `/webhook/openclaw` endpoint on ingestion server
- OpenClaw sends events → we log them
- For outbound: call OpenClaw's local REST API to dispatch messages via its connected channels

### 5. iCloud backup path
`~/Library/Mobile Documents/com~apple~CloudDocs/Database Backups/memory-system/`

### 6. Supabase sync
- Every 6 hours: `pg_dump` local DB → gzip → `psql` restore to Supabase connection string
- Supabase is a warm DR copy, NOT the primary

### 7. launchd for scheduling (not cron — macOS native)
- `memory.server` — always-on ingestion FastAPI server
- `memory.backup` — iCloud backup every 6 hours
- `memory.sync` — Supabase sync every 6 hours (offset by 3h from backup)
- `memory.digest` — daily digest at 07:00
- `memory.analysis` — analysis worker polls for pending work every 5 minutes

## File Structure

```
~/Workspace/memory-system/
├── .env.example
├── pyproject.toml
├── README.md
├── config/
│   ├── config.yaml
│   └── alert_rules.yaml
├── schema/
│   ├── 001_core.sql        ← memory_log, agents, sources, sessions
│   ├── 002_semantic.sql    ← gists, obsidian_notes, concept_links
│   ├── 003_alerts.sql      ← alert_rules, alert_history
│   └── 004_indexes.sql     ← performance + full-text search
├── scripts/
│   ├── setup.sh            ← one-shot installer
│   ├── teardown.sh         ← clean uninstall
│   ├── backup.sh           ← manual backup trigger
│   └── status.sh           ← health check
├── ingestion/
│   ├── __init__.py
│   ├── base.py             ← BaseIngester ABC
│   ├── agent_memory.py     ← MemoryClient SDK (import this in agents)
│   ├── haos_ingestion.py   ← Home Assistant webhook handler
│   ├── social_ingestion.py ← Social API polling/webhook
│   ├── file_import.py      ← Bulk CSV/JSON import
│   └── server.py           ← FastAPI ingestion server
├── analysis/
│   ├── __init__.py
│   ├── llm_client.py       ← Configurable LLM factory
│   ├── gist_generator.py   ← Extract semantic gists
│   ├── event_analyzer.py   ← Event-triggered analysis
│   ├── query_engine.py     ← On-demand queries
│   └── digest.py           ← Periodic digest generation
├── obsidian/
│   ├── __init__.py
│   ├── writer.py           ← Write/update Obsidian notes
│   ├── graph.py            ← Manage links and graph structure
│   └── templates/
│       ├── event_card.md
│       ├── analysis_report.md
│       ├── daily_digest.md
│       └── query_result.md
├── notifications/
│   ├── __init__.py
│   ├── telegram_bot.py     ← Telegram bot integration
│   ├── openclaw_dispatch.py← Send via OpenClaw channels
│   └── dispatcher.py       ← Unified dispatcher
├── sync/
│   ├── backup.py           ← iCloud backup logic
│   └── supabase_sync.py    ← Supabase periodic sync
├── launchd/
│   ├── memory.server.plist
│   ├── memory.backup.plist
│   ├── memory.sync.plist
│   ├── memory.digest.plist
│   └── memory.analysis.plist
└── tests/
    ├── test_ingestion.py
    ├── test_analysis.py
    └── test_obsidian.py
```

## Implementation Order
1. Schema → 2. Config → 3. Agent SDK → 4. Ingestion server → 5. Sync/backup scripts
→ 6. Analysis engine → 7. Obsidian writer → 8. Notifications → 9. launchd plists
→ 10. setup.sh → 11. Tests → 12. README
