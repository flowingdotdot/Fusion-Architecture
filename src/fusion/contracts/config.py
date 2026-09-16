"""Config revision contract (doc section 14-15): Stage/Apply are separated from
each other on purpose -- Stage is always allowed (even mid-Show), Apply is gated by
whatever the owning Runtime decides "safe to apply" means right now.

Only the pieces every Runtime kind (Motor/Video/Scheduler/Setting) shares are
modeled here: an opaque, hashed, immutable-once-staged revision, and a journal
entry per Apply attempt. Doc section 14's actual config *categories* (project vs
site-binding vs install-settings revisions) are represented as a plain ``kind``
string rather than a fixed enum, since which kinds a given Runtime accepts is that
Runtime's own concern, not something this shared contract should hardcode.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ConfigRevision(BaseModel):
    revision_id: str
    kind: str
    content_hash: str
    content: dict[str, Any]
    staged_at: float


class ApplyOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    """Apply conditions weren't met right now (doc: "Show 정지·제어권·장비 상태 등
    적용 조건") -- the staged revision is preserved, nothing changed."""
    FAILED = "FAILED"
    """Apply was attempted and something broke partway; doc: "실패 시 사용 가능하다고
    표시하지 않는다" -- the active revision must NOT be advanced in this case."""


class ApplyRecord(BaseModel):
    revision_id: str
    outcome: ApplyOutcome
    detail: str | None = None
    applied_at: float
