"""
dispatcher.py — Unified alert dispatcher.

Reads channel config from config.yaml and routes messages to:
  - telegram
  - openclaw (WhatsApp, iMessage, etc. via OpenClaw)
  - obsidian (writes an alert note)

Usage:
  from notifications.dispatcher import dispatch_alert
  await dispatch_alert("🚨 Motion detected!", channels=["telegram", "obsidian"])
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from ingestion.base import get_conn, CONFIG

log = logging.getLogger(__name__)

_DEFAULT_CHANNELS = CONFIG.get("notifications", {}).get("channels_by_importance", {}).get("4", ["telegram"])


async def dispatch_alert(
    message: str,
    *,
    channels: list[str] | None = None,
    rule_name: str | None = None,
    event_id: int | None = None,
    importance: int = 4,
    metadata: dict | None = None,
) -> dict[str, bool]:
    """
    Send an alert to one or more channels.

    Parameters
    ----------
    message : str
        The alert message (Markdown supported).
    channels : list[str]
        Which channels to use. Defaults to config-driven channels for importance level.
    rule_name : str, optional
        Name of the alert rule that triggered this.
    event_id : int, optional
        memory_log id of the triggering event.
    importance : int
        1-5 importance level.
    metadata : dict, optional
        Extra metadata stored in alert_history.

    Returns
    -------
    dict mapping channel → success (bool)
    """
    if channels is None:
        imp_map = CONFIG.get("notifications", {}).get("channels_by_importance", {})
        channels = imp_map.get(str(importance), _DEFAULT_CHANNELS)

    results: dict[str, bool] = {}

    for channel in channels:
        channel = channel.strip().lower()
        try:
            if channel == "telegram":
                results[channel] = await _send_telegram(message)
            elif channel == "openclaw":
                results[channel] = await _send_openclaw(message)
            elif channel == "obsidian":
                results[channel] = await _send_obsidian(message, importance, event_id)
            else:
                log.warning("Unknown channel: %s", channel)
                results[channel] = False
        except Exception as e:
            log.error("Dispatch to %s failed: %s", channel, e)
            results[channel] = False

    # Record in alert_history
    _record_alert(message, results, rule_name, event_id, metadata or {})

    return results


async def _send_telegram(message: str) -> bool:
    from .telegram_bot import get_notifier
    notifier = get_notifier()
    return await notifier.send(message)


async def _send_openclaw(message: str) -> bool:
    from .openclaw_dispatch import get_dispatcher
    dispatcher = get_dispatcher()
    # Check if OpenClaw is up; gracefully skip if not
    if not await dispatcher.health_check():
        log.warning("OpenClaw offline — skipping dispatch")
        return False
    return await dispatcher.send(message, channel="telegram", msg_type="alert")


async def _send_obsidian(message: str, importance: int, event_id: int | None) -> bool:
    try:
        from obsidian.writer import write_alert_note
        # Extract a title from the first line
        first_line = message.split("\n")[0]
        title = re.sub(r"[*_`#]", "", first_line).strip()[:100] or "Alert"
        await write_alert_note(
            title=title,
            content=message,
            source="system",
            importance=importance,
            event_id=event_id,
        )
        return True
    except Exception as e:
        log.error("Obsidian alert write failed: %s", e)
        return False


def _record_alert(
    message: str,
    results: dict[str, bool],
    rule_name: str | None,
    event_id: int | None,
    metadata: dict,
) -> None:
    """Persist alert dispatch record to alert_history."""
    try:
        import psycopg
        channels_sent = [ch for ch, ok in results.items() if ok]
        success = any(results.values())
        with get_conn() as conn:
            # Resolve rule_id if rule_name provided
            rule_id = None
            if rule_name:
                row = conn.execute(
                    "SELECT id FROM alert_rules WHERE name = %s", (rule_name,)
                ).fetchone()
                rule_id = row["id"] if row else None

            conn.execute(
                """INSERT INTO alert_history
                    (rule_id, memory_log_id, message, channels_sent, success, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    rule_id,
                    event_id,
                    message[:2000],
                    channels_sent,
                    success,
                    psycopg.types.json.Jsonb(metadata),
                ),
            )
    except Exception as e:
        log.error("Failed to record alert_history: %s", e)


# ---------------------------------------------------------------------------
# Alert rule engine
# ---------------------------------------------------------------------------

async def evaluate_event_against_rules(event_id: int) -> list[str]:
    """
    Check a new event against all active alert rules.
    Returns list of rule names that fired.
    """
    import re as _re

    with get_conn() as conn:
        event_row = conn.execute(
            """SELECT ml.*, s.name AS source_name
               FROM memory_log ml
               LEFT JOIN sources s ON s.id = ml.source_id
               WHERE ml.id = %s""",
            (event_id,),
        ).fetchone()
        if not event_row:
            return []

        rules = conn.execute(
            "SELECT * FROM alert_rules WHERE enabled = TRUE"
        ).fetchall()

    event = dict(event_row)
    fired: list[str] = []

    for rule in rules:
        rule = dict(rule)
        if not _rule_matches(rule, event):
            continue

        # Check cooldown
        with get_conn() as conn:
            recent = conn.execute(
                """SELECT COUNT(*) AS n FROM alert_history
                   WHERE rule_id = %s
                   AND fired_at > NOW() - INTERVAL '%s seconds'""",
                (rule["id"], rule.get("cooldown_secs", 300)),
            ).fetchone()
            if recent["n"] > 0:
                continue

        # Fire!
        channels = rule.get("channels", ["telegram"])
        msg = _build_rule_message(rule, event)
        await dispatch_alert(
            msg,
            channels=channels,
            rule_name=rule["name"],
            event_id=event_id,
            importance=event.get("importance", 3),
        )
        fired.append(rule["name"])

    return fired


def _rule_matches(rule: dict, event: dict) -> bool:
    """Check if an event matches a rule's conditions."""
    # Filter by sources
    sources = rule.get("sources")
    if sources and event.get("source_name") not in sources:
        return False

    # Filter by event_types
    event_types = rule.get("event_types")
    if event_types and event.get("event_type") not in event_types:
        return False

    # Filter by importance
    imp_min = rule.get("importance_min", 1)
    if event.get("importance", 1) < imp_min:
        return False

    condition = rule.get("condition", {})
    rule_type = rule.get("rule_type", "")
    content = event.get("content", "").lower()

    if rule_type == "threshold":
        field = condition.get("field", "importance")
        op = condition.get("operator", "gte")
        val = condition.get("value", 5)
        ev_val = event.get(field, 0)
        if op == "gte": return ev_val >= val
        if op == "lte": return ev_val <= val
        if op == "eq":  return ev_val == val

    elif rule_type == "keyword":
        keywords = [k.lower() for k in condition.get("keywords", [])]
        match_mode = condition.get("match", "any")
        if match_mode == "any":
            return any(k in content for k in keywords)
        return all(k in content for k in keywords)

    elif rule_type == "pattern":
        pattern = condition.get("pattern", "")
        flags_str = condition.get("flags", "")
        flags = 0
        if "i" in flags_str: flags |= re.IGNORECASE
        return bool(re.search(pattern, content, flags))

    return False


def _build_rule_message(rule: dict, event: dict) -> str:
    source = event.get("source_name", "unknown")
    ts = event.get("event_time", datetime.now(timezone.utc))
    if hasattr(ts, "strftime"):
        ts = ts.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"🔔 *{rule.get('name', 'Alert')}*\n"
        f"Rule: _{rule.get('description', '')}_ \n\n"
        f"{event.get('content', '')}\n\n"
        f"Source: {source} | {ts} | Importance: {event.get('importance',3)}/5"
    )
