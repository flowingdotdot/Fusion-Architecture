"""Structured error contract shared by every Fusion Runtime API surface.

Every rejection carries a stable ``ErrorCode`` plus a human message and optional
machine-readable ``details`` -- callers (Scheduler, tests, future Studio UI) branch
on ``code``, never on message text.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNKNOWN_TARGET = "UNKNOWN_TARGET"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    BUSY = "BUSY"
    REQUEST_ID_CONFLICT = "REQUEST_ID_CONFLICT"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    BLOB_NOT_FOUND = "BLOB_NOT_FOUND"
    CONTROL_SESSION_REQUIRED = "CONTROL_SESSION_REQUIRED"
    CONTROL_SESSION_INVALID = "CONTROL_SESSION_INVALID"
    CONTROL_SESSION_EXPIRED = "CONTROL_SESSION_EXPIRED"
    RUNTIME_BOOT_MISMATCH = "RUNTIME_BOOT_MISMATCH"
    STALE_GENERATION = "STALE_GENERATION"
    RESYNC_REQUIRED = "RESYNC_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class FusionError(Exception):
    """Raised by Runtime/Core code for any rejection that must reach a caller intact."""

    def __init__(
        self, code: ErrorCode, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}
