"""
test_obsidian.py — Tests for Obsidian note writing.
Uses a temporary vault path to avoid writing to the real vault.
"""
import pytest
import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    """Override OBSIDIAN_VAULT_PATH to a temp directory."""
    vault = tmp_path / "TestVault"
    vault.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    return vault


@pytest.fixture
def mock_db(monkeypatch):
    """Mock get_conn so tests don't need a real DB."""
    from unittest.mock import MagicMock, patch

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute = MagicMock(return_value=mock_conn)
    mock_conn.fetchone = MagicMock(return_value=None)

    with patch("obsidian.writer.get_conn", return_value=mock_conn):
        yield mock_conn


@pytest.mark.asyncio
async def test_write_digest_note(temp_vault, mock_db):
    from obsidian.writer import write_digest_note

    path = await write_digest_note(
        gist_id=1,
        content="## Summary\nA productive day.",
        target_date=date(2026, 2, 21),
        period="daily",
    )

    assert path is not None
    note_file = temp_vault / "Memory" / "Digests" / "Daily" / "2026-02-21.md"
    assert note_file.exists()
    content = note_file.read_text()
    assert "Daily Digest" in content
    assert "A productive day." in content
    assert "---" in content  # has frontmatter


@pytest.mark.asyncio
async def test_write_event_card(temp_vault, mock_db):
    from obsidian.writer import write_event_card

    event = {
        "id": 42,
        "event_time": datetime(2026, 2, 21, 10, 30, 0, tzinfo=timezone.utc),
        "event_type": "haos_state_changed",
        "content": "HAOS: Front Door → on (binary_sensor.front_door)",
        "source_name": "haos",
        "importance": 4,
    }
    analysis = {
        "title": "Front Door Opened",
        "assessment": "The front door was opened at 10:30 UTC.",
        "implications": ["Someone entered the home"],
        "follow_up": ["Check camera footage"],
        "severity": "medium",
        "tags": ["home", "security"],
    }

    path = await write_event_card(gist_id=1, analysis=analysis, event=event)

    assert path is not None
    # Find the file
    event_files = list((temp_vault / "Memory" / "Events").glob("*.md"))
    assert len(event_files) == 1
    content = event_files[0].read_text()
    assert "Front Door Opened" in content
    assert "Someone entered the home" in content
    assert "Check camera footage" in content


@pytest.mark.asyncio
async def test_write_query_note(temp_vault, mock_db):
    from obsidian.writer import write_query_note

    events = [
        {"id": 1, "event_time": datetime.now(timezone.utc), "content": "event 1", "source_name": "haos"},
    ]
    path = await write_query_note(
        gist_id=2,
        question="What happened at home last week?",
        answer="## Answer\nThe door was opened 3 times.",
        events=events,
    )

    query_files = list((temp_vault / "Memory" / "Queries").glob("*.md"))
    assert len(query_files) == 1
    content = query_files[0].read_text()
    assert "What happened at home last week?" in content
    assert "door was opened" in content


@pytest.mark.asyncio
async def test_write_alert_note(temp_vault, mock_db):
    from obsidian.writer import write_alert_note

    path = await write_alert_note(
        title="Motion Detected",
        content="Motion was detected at the front door at 02:30 AM.",
        source="haos",
        importance=4,
        event_id=99,
    )

    alert_files = list((temp_vault / "Memory" / "Alerts").glob("*.md"))
    assert len(alert_files) == 1
    content = alert_files[0].read_text()
    assert "Motion Detected" in content
    assert "02:30 AM" in content


def test_slug():
    from obsidian.writer import _slug
    assert _slug("Hello World!") == "hello-world"
    assert _slug("  Multiple   Spaces  ") == "multiple-spaces"
    assert _slug("Special @#$ Chars") == "special-chars"


def test_frontmatter():
    from obsidian.writer import _frontmatter
    fm = _frontmatter("test", "My Title", ["tag1", "#tag2"])
    assert "type: test" in fm
    assert 'title: "My Title"' in fm
    assert "tag1" in fm
    assert "tag2" in fm  # stripped the #
