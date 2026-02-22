"""
haos_ingestion.py — Home Assistant OS (HAOS) event ingestion.

Home Assistant side: create a Webhook automation that POSTs to
  http://YOUR_MAC_IP:8765/ingest/haos
with headers:
  X-HAOS-Secret: <HAOS_WEBHOOK_SECRET from .env>

Payload format expected:
{
  "event_type": "state_changed",
  "entity_id": "binary_sensor.front_door",
  "state": "on",
  "attributes": {...},
  "timestamp": "2026-02-21T07:30:00+00:00"
}
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone

from .base import BaseIngester, insert_event

log = logging.getLogger(__name__)

# HAOS event_type → importance mapping
_IMPORTANCE_MAP: dict[str, int] = {
    "alarm_trigger":         5,
    "alarm_disarmed":        4,
    "alarm_armed":           3,
    "lock_jammed":           5,
    "motion_detected":       3,
    "door_opened":           3,
    "person_detected":       4,
    "smoke_detected":        5,
    "water_leak_detected":   5,
    "default":               2,
}

_ALERT_KEYWORDS = {
    "alarm", "intrusion", "smoke", "fire", "leak", "water", "lock", "motion",
    "person", "door", "window", "glass", "break",
}


class HAOSIngester(BaseIngester):
    """Ingest events from Home Assistant."""

    source_name = "haos"
    source_type = "device"

    def __init__(self):
        super().__init__()
        self._secret = os.environ.get("HAOS_WEBHOOK_SECRET", "")

    def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verify X-HAOS-Secret header."""
        if not self._secret:
            return True  # not configured — skip check
        expected = hmac.new(self._secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def ingest(self, data: dict) -> list[int]:  # type: ignore[override]
        """Process one HAOS webhook payload and store it."""
        event_type = data.get("event_type", "state_changed")
        entity_id = data.get("entity_id", "unknown")
        state = data.get("state", "")
        attributes = data.get("attributes", {})
        friendly_name = attributes.get("friendly_name", entity_id)

        # Determine importance
        importance = _IMPORTANCE_MAP.get("default", 2)
        for keyword in _ALERT_KEYWORDS:
            if keyword in entity_id.lower() or keyword in friendly_name.lower():
                importance = max(importance, _IMPORTANCE_MAP.get(f"{keyword}_detected", 3))

        # Parse timestamp
        ts_raw = data.get("timestamp") or data.get("last_changed")
        event_time: datetime
        if ts_raw:
            try:
                event_time = datetime.fromisoformat(str(ts_raw))
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
            except ValueError:
                event_time = datetime.now(timezone.utc)
        else:
            event_time = datetime.now(timezone.utc)

        content = f"HAOS: {friendly_name} → {state} ({entity_id})"
        tags = ["haos", f"entity:{entity_id}", f"domain:{entity_id.split('.')[0]}"]

        # Extra tags for alert events
        for keyword in _ALERT_KEYWORDS:
            if keyword in content.lower():
                tags.append("alert")
                importance = max(importance, 4)
                break

        event_id = self.store(
            content=content,
            event_type=f"haos_{event_type}",
            event_time=event_time,
            importance=importance,
            tags=tags,
            payload=data,
        )
        log.info("HAOS event stored: id=%s entity=%s state=%s", event_id, entity_id, state)
        return [event_id]
