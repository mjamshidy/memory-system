"""
test_ingestion.py — Tests for the ingestion layer.
Run: pytest tests/test_ingestion.py -v
"""
import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# DB fixture — uses the real local DB if available, otherwise skips
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def db_available():
    """Check if the test DB is accessible."""
    try:
        from ingestion.base import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.fixture
def conn(db_available):
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.base import get_conn
    with get_conn() as c:
        yield c


# ---------------------------------------------------------------------------
# insert_event
# ---------------------------------------------------------------------------

def test_insert_event_basic(db_available):
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.base import insert_event, get_conn
    event_id = insert_event(
        content="Test event from pytest",
        event_type="test",
        source_name="system",
        importance=2,
        tags=["test", "pytest"],
    )
    assert isinstance(event_id, int)
    assert event_id > 0

    # Verify it was written
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM memory_log WHERE id = %s", (event_id,)
        ).fetchone()
    assert row is not None
    assert row["content"] == "Test event from pytest"
    assert row["event_type"] == "test"
    assert row["importance"] == 2


def test_insert_event_append_only(db_available):
    """Verify UPDATE is silently blocked by the RULE."""
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.base import insert_event, get_conn

    event_id = insert_event(
        content="Original content",
        event_type="test",
        source_name="system",
    )

    with get_conn() as conn:
        # UPDATE should be silently ignored (RULE returns NOTHING)
        conn.execute(
            "UPDATE memory_log SET content = 'Modified' WHERE id = %s",
            (event_id,),
        )
        row = conn.execute(
            "SELECT content FROM memory_log WHERE id = %s", (event_id,)
        ).fetchone()
    # Content should be unchanged
    assert row["content"] == "Original content"


def test_insert_event_delete_blocked(db_available):
    """Verify DELETE is silently blocked."""
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.base import insert_event, get_conn

    event_id = insert_event(
        content="Should not be deleted",
        event_type="test",
        source_name="system",
    )
    with get_conn() as conn:
        conn.execute("DELETE FROM memory_log WHERE id = %s", (event_id,))
        row = conn.execute(
            "SELECT id FROM memory_log WHERE id = %s", (event_id,)
        ).fetchone()
    assert row is not None  # still there!


# ---------------------------------------------------------------------------
# MemoryClient SDK
# ---------------------------------------------------------------------------

def test_memory_client_log(db_available):
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.agent_memory import MemoryClient

    with MemoryClient(agent="claude", session_label="pytest-test") as mem:
        event_id = mem.log("Test memory log entry", importance=2, tags=["test"])
        assert isinstance(event_id, int)

        history = mem.session_history()
        assert len(history) >= 1
        assert any(e["id"] == event_id for e in history)


def test_memory_client_recall(db_available):
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.agent_memory import MemoryClient

    unique_phrase = f"unique-search-phrase-{uuid.uuid4().hex[:8]}"
    with MemoryClient(agent="claude") as mem:
        mem.log(f"This contains {unique_phrase} for testing recall")
        results = mem.recall(unique_phrase)
        assert len(results) >= 1
        assert any(unique_phrase in r["content"] for r in results)


def test_memory_client_context_window(db_available):
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.agent_memory import MemoryClient

    with MemoryClient(agent="claude") as mem:
        mem.log("Context window test entry")
        ctx = mem.context_window(max_chars=1000)
        assert isinstance(ctx, str)


# ---------------------------------------------------------------------------
# HAOS Ingester
# ---------------------------------------------------------------------------

def test_haos_ingester(db_available):
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.haos_ingestion import HAOSIngester

    ingester = HAOSIngester()
    ids = ingester.ingest({
        "event_type": "state_changed",
        "entity_id": "binary_sensor.front_door",
        "state": "on",
        "attributes": {"friendly_name": "Front Door"},
        "timestamp": "2026-02-21T10:00:00+00:00",
    })
    assert len(ids) == 1
    assert isinstance(ids[0], int)


def test_haos_security_event_importance(db_available):
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.haos_ingestion import HAOSIngester
    from ingestion.base import get_conn

    ingester = HAOSIngester()
    ids = ingester.ingest({
        "event_type": "state_changed",
        "entity_id": "alarm_control_panel.home",
        "state": "triggered",
        "attributes": {"friendly_name": "Home Alarm"},
    })
    with get_conn() as conn:
        row = conn.execute(
            "SELECT importance FROM memory_log WHERE id = %s", (ids[0],)
        ).fetchone()
    assert row["importance"] >= 4


# ---------------------------------------------------------------------------
# Telegram Ingester
# ---------------------------------------------------------------------------

def test_telegram_ingester(db_available):
    if not db_available:
        pytest.skip("Database not available")
    from ingestion.social_ingestion import TelegramIngester

    ingester = TelegramIngester()
    ids = ingester.ingest({
        "update_id": 12345,
        "message": {
            "message_id": 1,
            "date": 1740130800,
            "chat": {"id": -100123, "title": "Test Chat"},
            "from": {"id": 456, "first_name": "Test", "username": "testuser"},
            "text": "Hello from Telegram test",
        },
    })
    assert len(ids) == 1


# ---------------------------------------------------------------------------
# File import (dry run)
# ---------------------------------------------------------------------------

def test_file_import_csv(tmp_path, db_available):
    if not db_available:
        pytest.skip("Database not available")
    import csv
    from ingestion.file_import import _parse_csv

    csv_file = tmp_path / "test.csv"
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["content", "date"])
        writer.writeheader()
        writer.writerow({"content": "CSV row 1", "date": "2026-02-21"})
        writer.writerow({"content": "CSV row 2", "date": "2026-02-21"})

    records = list(_parse_csv(csv_file, "test_source", "import"))
    assert len(records) == 2
    assert records[0]["content"] == "CSV row 1"
