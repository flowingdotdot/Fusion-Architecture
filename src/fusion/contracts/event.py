"""Event contract (doc section 8): a fact that already happened."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    protocol_version: int = 1
    event_id: str
    type: str
    runtime_id: str
    runtime_boot_id: str
    sequence: int
    target_id: str | None = None
    command_id: str | None = None
    run_id: str | None = None
    cue_id: str | None = None
    occurred_at: float
    payload: dict[str, Any] = Field(default_factory=dict)
