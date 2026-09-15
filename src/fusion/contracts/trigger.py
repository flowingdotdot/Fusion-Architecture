"""Trigger contract (doc section 11/13): registered source/type/comparison/show-state
conditions only -- no eval, no arbitrary scripts. An external OSC/UDP transport that
turns wire messages into ``InputEvent``s is stage 6 scope; this module only defines
the event shape and the condition a Trigger matches against it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Comparator = Literal["eq", "ne", "gt", "gte", "lt", "lte"]
TriggerAction = Literal["start", "hold", "abort"]


class InputEvent(BaseModel):
    source: str
    type: str
    value: float | str | bool = True
    occurred_at: float


class TriggerCondition(BaseModel):
    source: str
    type: str
    comparator: Comparator = "eq"
    value: float | str | bool = True


class Trigger(BaseModel):
    trigger_id: str
    condition: TriggerCondition
    action: TriggerAction
    show_states: list[str] | None = None
    """If set, the Trigger only fires while the Show is in one of these states --
    e.g. a "start" trigger normally only makes sense from ARMED. ``None`` means "any
    state" (ShowController's own reentry guard still applies)."""
    debounce_ms: float = 0.0
    """Ignore further matching events from the same source within this window of the
    last matching event (doc section 11: "버튼 debounce")."""
    cooldown_ms: float = 0.0
    """After this Trigger fires, ignore further matches for this long (doc section
    11: "시작 연타 재진입 방지", "전이 반복 제한")."""


def condition_matches(condition: TriggerCondition, event: InputEvent) -> bool:
    if condition.source != event.source or condition.type != event.type:
        return False
    try:
        if condition.comparator == "eq":
            return bool(event.value == condition.value)
        if condition.comparator == "ne":
            return bool(event.value != condition.value)
        # Ordering comparators only make sense for numeric values.
        a, b = float(event.value), float(condition.value)
        if condition.comparator == "gt":
            return a > b
        if condition.comparator == "gte":
            return a >= b
        if condition.comparator == "lt":
            return a < b
        if condition.comparator == "lte":
            return a <= b
    except (TypeError, ValueError):
        return False
    return False
