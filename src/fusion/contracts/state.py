"""Target state contract (doc section 8): every field carries its own observation time
and quality. Unknown values are never silently replaced with 0/false/READY.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

Quality = Literal["GOOD", "STALE", "UNKNOWN"]


class ObservedValue(BaseModel):
    value: Any
    observed_at: float
    quality: Quality = "GOOD"


class TargetState(BaseModel):
    target_id: str
    connection: Literal["CONNECTED", "DISCONNECTED"] = "CONNECTED"
    fields: dict[str, ObservedValue]
