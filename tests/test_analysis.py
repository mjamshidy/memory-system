"""
test_analysis.py — Tests for the analysis layer.
Most tests mock LLM calls to avoid API costs during CI.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

def test_get_llm_claude():
    from analysis.llm_client import get_llm, AnthropicLLM
    llm = get_llm("claude:claude-haiku-4-5-20251001")
    assert isinstance(llm, AnthropicLLM)
    assert llm.model == "claude-haiku-4-5-20251001"


def test_get_llm_openai():
    from analysis.llm_client import get_llm, OpenAILLM
    llm = get_llm("openai:gpt-4o-mini")
    assert isinstance(llm, OpenAILLM)


def test_get_llm_ollama():
    from analysis.llm_client import get_llm, OllamaLLM
    llm = get_llm("ollama:llama3.1")
    assert isinstance(llm, OllamaLLM)


def test_get_llm_unknown():
    from analysis.llm_client import get_llm
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_llm("unknownprovider")


# ---------------------------------------------------------------------------
# Gist generator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_gist_mocked():
    """Test gist generation with a mocked LLM."""
    from analysis.gist_generator import generate_gist

    mock_response = {
        "title": "Test gist",
        "summary": "A summary of test events.",
        "facts": ["Fact 1", "Fact 2"],
        "patterns": [],
        "tags": ["test"],
        "importance": 3,
        "gist_type": "summary",
    }

    mock_llm = AsyncMock()
    mock_llm.complete_json = AsyncMock(return_value=mock_response)

    events = [
        {
            "id": 1,
            "event_time": datetime.now(timezone.utc),
            "event_type": "test",
            "content": "Test event content",
            "source_name": "system",
            "importance": 3,
        }
    ]

    with patch("analysis.gist_generator.get_llm", return_value=mock_llm):
        result = await generate_gist(events)

    assert result["title"] == "Test gist"
    assert len(result["facts"]) == 2


@pytest.mark.asyncio
async def test_generate_gist_llm_failure_fallback():
    """If LLM fails, should return a fallback gist."""
    from analysis.gist_generator import generate_gist

    mock_llm = AsyncMock()
    mock_llm.complete_json = AsyncMock(side_effect=Exception("API error"))

    events = [
        {
            "id": 1,
            "event_time": datetime.now(timezone.utc),
            "event_type": "test",
            "content": "Fallback test content",
            "source_name": "system",
            "importance": 3,
        }
    ]

    with patch("analysis.gist_generator.get_llm", return_value=mock_llm):
        result = await generate_gist(events)

    assert result["title"] is not None
    assert "Fallback test content" in result["summary"] or len(result["summary"]) > 0


# ---------------------------------------------------------------------------
# Alert dispatcher rules
# ---------------------------------------------------------------------------

def test_rule_matches_threshold():
    from notifications.dispatcher import _rule_matches
    rule = {
        "rule_type": "threshold",
        "condition": {"field": "importance", "operator": "gte", "value": 5},
        "sources": None,
        "event_types": None,
        "importance_min": 1,
    }
    assert _rule_matches(rule, {"importance": 5, "content": "", "source_name": "haos"})
    assert not _rule_matches(rule, {"importance": 4, "content": "", "source_name": "haos"})


def test_rule_matches_keyword():
    from notifications.dispatcher import _rule_matches
    rule = {
        "rule_type": "keyword",
        "condition": {"keywords": ["alarm", "motion"], "match": "any"},
        "sources": None,
        "event_types": None,
        "importance_min": 1,
    }
    assert _rule_matches(rule, {"importance": 3, "content": "alarm triggered", "source_name": "haos"})
    assert not _rule_matches(rule, {"importance": 3, "content": "everything is fine", "source_name": "haos"})


def test_rule_matches_pattern():
    from notifications.dispatcher import _rule_matches
    rule = {
        "rule_type": "pattern",
        "condition": {"pattern": r"backup_failed|sync_failed", "flags": "i"},
        "sources": ["system"],
        "event_types": None,
        "importance_min": 1,
    }
    assert _rule_matches(rule, {"importance": 4, "content": "BACKUP_FAILED: disk full", "source_name": "system"})
    assert not _rule_matches(rule, {"importance": 4, "content": "backup_success", "source_name": "system"})


def test_rule_source_filter():
    from notifications.dispatcher import _rule_matches
    rule = {
        "rule_type": "keyword",
        "condition": {"keywords": ["door"], "match": "any"},
        "sources": ["haos"],  # only HAOS
        "event_types": None,
        "importance_min": 1,
    }
    # should match haos source
    assert _rule_matches(rule, {"importance": 3, "content": "front door opened", "source_name": "haos"})
    # should NOT match telegram source
    assert not _rule_matches(rule, {"importance": 3, "content": "door mention in chat", "source_name": "telegram"})


# ---------------------------------------------------------------------------
# Query engine
# ---------------------------------------------------------------------------

def test_extract_keywords():
    from analysis.query_engine import _extract_keywords
    kw = _extract_keywords("What happened with HAOS last week?")
    assert "haos" in kw
    assert "last" in kw or "week" in kw
    assert "what" not in kw  # stopword


@pytest.mark.asyncio
async def test_answer_query_no_events(db_available=None):
    """With no matching events, should return 'no events found' gracefully."""
    from analysis.query_engine import answer_query

    mock_llm = AsyncMock()

    with patch("analysis.query_engine.get_llm", return_value=mock_llm), \
         patch("analysis.query_engine._search_events", return_value=[]):
        result = await answer_query("xyzzy-nonexistent-topic", write_to_obsidian=False)

    assert result["event_count"] == 0
    assert "no events" in result["answer"].lower() or "no" in result["answer"].lower()
