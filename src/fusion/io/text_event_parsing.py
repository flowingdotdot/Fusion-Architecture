"""Shared default parser for the line/datagram-oriented input Endpoints (UDP,
Serial): doc section 13 leaves the exact payload format up to the real sender, so
this is a permissive default, not a fixed protocol -- a JSON object with "type"
(and optional "value") if the text parses as one, otherwise the whole trimmed text
becomes the event type with ``value=True`` (the simplest possible sender: an
Arduino or netcat just writes a bare keyword).
"""

from __future__ import annotations

import json

from fusion.contracts.trigger import InputEvent
from fusion.core.clock import Clock


def parse_text_event(text: str, *, source: str, clock: Clock) -> InputEvent | None:
    trimmed = text.strip()
    if not trimmed:
        return None
    try:
        payload = json.loads(trimmed)
        event_type = str(payload["type"])
        value = payload.get("value", True)
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        event_type = trimmed
        value = True
    return InputEvent(source=source, type=event_type, value=value, occurred_at=clock.now())
