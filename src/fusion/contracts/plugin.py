"""Plugin manifest contract (doc section 9): what a Target publicly exposes.

Only fields needed to drive stage 1-2 execution/validation are modeled here.
Manufacturer-specific detail (units, physical ranges, retry policy) is added when a
real Plugin is built against real vendor documentation (stage 4) -- nothing here is
a stand-in for unconfirmed hardware specs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ActionPriority = Literal["normal", "control"]


class ActionSpec(BaseModel):
    name: str
    params_schema: dict[str, Any] = Field(default_factory=dict)
    max_completion: Literal["dispatched", "acknowledged", "observed"] = "observed"
    priority: ActionPriority = "normal"
    """"control" actions (e.g. stop) bypass the busy/reject rule normal actions get."""


class TargetManifest(BaseModel):
    target_id: str
    plugin_id: str
    plugin_version: str
    actions: list[ActionSpec]
    capabilities: list[str] = Field(default_factory=list)

    def get_action(self, name: str) -> ActionSpec | None:
        return next((a for a in self.actions if a.name == name), None)
