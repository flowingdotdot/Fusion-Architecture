"""ID generation. IDs are opaque strings -- never IP addresses or display names."""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def new_command_id() -> str:
    return new_id("cmd")


def new_event_id() -> str:
    return new_id("evt")


def new_boot_id() -> str:
    return new_id("boot")


def new_control_session_id() -> str:
    return new_id("session")
