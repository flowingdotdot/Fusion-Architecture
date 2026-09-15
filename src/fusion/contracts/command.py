"""Command contract (doc section 7): submit request, tracked record, lifecycle enums."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CompletionRequirement(StrEnum):
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    OBSERVED = "observed"


class CommandStatus(StrEnum):
    QUEUED = "QUEUED"
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    OBSERVING = "OBSERVING"
    TERMINAL = "TERMINAL"


class CommandOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class CommandSubmitRequest(BaseModel):
    protocol_version: int = 1
    request_id: str
    control_session_id: str | None = None
    run_id: str | None = None
    cue_id: str | None = None
    show_generation: int | None = None
    target_id: str
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    completion_requirement: CompletionRequirement = CompletionRequirement.OBSERVED
    source: str = "manual"
    actor: str | None = None


class CommandRecord(BaseModel):
    command_id: str
    request_id: str
    runtime_id: str
    runtime_boot_id: str
    target_id: str
    action: str
    params: dict[str, Any]
    completion_requirement: CompletionRequirement
    status: CommandStatus
    outcome: CommandOutcome | None = None
    achieved_completion: CompletionRequirement | None = None
    execution_generation: int
    run_id: str | None = None
    cue_id: str | None = None
    source: str
    actor: str | None = None
    error: dict[str, Any] | None = None
    created_at: float
    updated_at: float


def fingerprint(req: CommandSubmitRequest) -> tuple[Any, ...]:
    """Stable identity of "what this request_id means" -- used to tell an idempotent
    resubmission (same fingerprint) apart from a request_id conflict (different one).
    Assumes JSON-primitive param values (stage 1-2 scope); nested dict/list param
    values would need a canonical serialization, not needed by Fake Motor yet.
    """
    return (
        req.target_id,
        req.action,
        tuple(sorted(req.params.items())),
        req.completion_requirement.value,
    )
