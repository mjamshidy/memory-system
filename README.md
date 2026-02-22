# Memory System

A two-layer personal AI memory infrastructure.

**Layer 1 (Episodic):** Append-only PostgreSQL log — every event, conversation, observation, and device signal ever recorded.

**Layer 2 (Semantic):** Obsidian as a liquid knowledge graph — AI-generated gists, analysis reports, daily digests, query results, and alerts, all interlinked.

## Architecture

```
[Sources: AI Agents, HAOS, Social, Files]
              ↓
    [Ingestion Server :8765]
              ↓
    [PostgreSQL 18 — Episodic Log]
        ↓              ↓              ↓
  [iCloud Backup] [Supabase Sync] [Analysis Engine]
                                       ↓
                           [Obsidian Vault (Semantic)]
                           [Telegram / OpenClaw Alerts]
```

## Quick Start

```bash
cd ~/Workspace/memory-system
cp .env.example .env
# Edit .env with your API keys
./scripts/setup.sh
```

## Data Sources

| Source | How it connects |
|--------|----------------|
| Claude / Codex / Gemini | `MemoryClient` SDK (`ingestion/agent_memory.py`) |
| OpenClaw | Webhook `POST /ingest/openclaw` |
| Home Assistant (HAOS) | Webhook `POST /ingest/haos` |
| Telegram | Bot webhook `POST /ingest/telegram` |
| GitHub | Webhook `POST /ingest/github` |
| Bulk imports | `python -m ingestion.file_import --format csv --file data.csv` |
| Generic | `POST /ingest/generic` |

## Using the Agent Memory SDK

```python
from ingestion.agent_memory import MemoryClient

# In any AI agent:
with MemoryClient(agent="claude", session_label="code-review") as mem:
    # Write
    mem.log("User asked about X")
    mem.log_action("web_search", {"query": "X", "results": [...]})
    mem.log_conversation(user_msg, assistant_response)

    # Read
    past = mem.recall("X", limit=5)
    ctx = mem.context_window(max_chars=4000)  # inject into prompt
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/stats` | DB statistics |
| POST | `/ingest/agent` | Agent memory write |
| POST | `/ingest/haos` | HAOS webhook |
| POST | `/ingest/telegram` | Telegram bot update |
| POST | `/ingest/github` | GitHub webhook |
| POST | `/ingest/openclaw` | OpenClaw events |
| POST | `/ingest/generic` | Any JSON source |
| POST | `/query` | Semantic search |

## Analysis & Digests

```bash
# Run analysis on unprocessed events
python -m analysis.gist_generator

# Generate today's digest manually
python -m analysis.digest daily

# Ask a question about your memory
python -m analysis.query_engine "What happened with HAOS this week?"
```

## Backups

- **iCloud:** Every 6 hours automatically via launchd. Manual: `./scripts/backup.sh`
- **Supabase:** Every 6 hours (offset 3h from iCloud). Requires `SUPABASE_DB_URL` in `.env`

## Scripts

| Script | Purpose |
|--------|---------|
| `./scripts/setup.sh` | Full install + launchd setup |
| `./scripts/teardown.sh` | Remove launchd services |
| `./scripts/status.sh` | Health check |
| `./scripts/backup.sh` | Manual backup |

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|----------|----------|-------------|
| `PG_DB` | No (default: memory_system) | PostgreSQL DB name |
| `ANTHROPIC_API_KEY` | For Claude gists | Claude API key |
| `OPENAI_API_KEY` | Optional | OpenAI API key |
| `TELEGRAM_BOT_TOKEN` | For alerts | Telegram bot token |
| `TELEGRAM_CHAT_ID` | For alerts | Your chat ID |
| `SUPABASE_DB_URL` | For remote backup | Supabase connection string |
| `OBSIDIAN_VAULT_PATH` | Auto-detected | Path to Obsidian vault |
| `OPENCLAW_API_URL` | Optional | OpenClaw local API |
| `HAOS_WEBHOOK_SECRET` | Recommended | HAOS webhook secret |

## Obsidian Structure

```
Memory/
├── Index.md              ← Master index (auto-updated)
├── Events/               ← High-importance event cards
├── Analysis/             ← Batch analysis reports
├── Digests/
│   ├── Daily/            ← Daily digests (one per day)
│   └── Weekly/           ← Weekly summaries
├── Queries/              ← On-demand query results
├── Alerts/               ← Critical alerts
├── Sources/              ← Per-source index notes
└── Concepts/             ← Graph concept nodes
```

## Portability

This repo is fully self-contained. To deploy on another machine:

```bash
git clone <this-repo>
cd memory-system
cp .env.example .env
# Fill in .env
./scripts/setup.sh
```

Requirements: macOS, PostgreSQL 18 (Homebrew), Python 3.11+
