"""Control session contract (doc section 5): one active control session per Runtime."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class RuntimeMode(StrEnum):
    MANUAL = "MANUAL"
    SHOW = "SHOW"
    MAINTENANCE = "MAINTENANCE"


class ControlSession(BaseModel):
    session_id: str
    mode: RuntimeMode
    actor: str
    issued_at: float
    expires_at: float
    runtime_boot_id: str
