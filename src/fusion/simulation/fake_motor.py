"""Fake Motor Target adapter (doc section 18/19 stage 1): no real I/O, driven by an
injected Clock so its motion can be simulated instantly under a FakeClock or in real
time under RealClock. Manufacturer specifics are intentionally NOT modeled here --
this exists purely to exercise the Command/Event/Session machinery before any real
Plugin exists (stage 4).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fusion.contracts.plugin import ActionSpec, TargetManifest
from fusion.contracts.state import ObservedValue, TargetState
from fusion.core.clock import Clock
from fusion.plugin_sdk.base import TargetAdapter

FaultMode = Literal["never_complete"] | None


class FakeMotorAdapter(TargetAdapter):
    VELOCITY_UNITS_PER_SEC = 200.0
    STEP_S = 0.05

    def __init__(self, target_id: str, clock: Clock, *, fault: FaultMode = None) -> None:
        self.target_id = target_id
        self._clock = clock
        self._fault = fault
        self.position = 0.0
        self.target_position = 0.0
        self.moving = False
        self.homed = True
        self.alarm = False
        self.move_count = 0
        self._move_task: asyncio.Task[None] | None = None

    def manifest(self) -> TargetManifest:
        return TargetManifest(
            target_id=self.target_id,
            plugin_id="fusion.simulation.fake_motor",
            plugin_version="0.1.0",
            actions=[
                ActionSpec(
                    name="move",
                    params_schema={"position": "number"},
                    max_completion="observed",
                    priority="normal",
                ),
                ActionSpec(
                    name="stop", params_schema={}, max_completion="acknowledged", priority="control"
                ),
            ],
            capabilities=["stop_motion"],
        )

    async def execute(self, action: str, params: dict[str, Any]) -> None:
        if action == "move":
            if self.alarm:
                raise ValueError("alarm active")
            self.target_position = float(params["position"])
            self.moving = True
            self.move_count += 1
            if self._move_task is not None:
                self._move_task.cancel()
            self._move_task = asyncio.create_task(self._run_move())
        elif action == "stop":
            if self._move_task is not None:
                self._move_task.cancel()
                self._move_task = None
            self.moving = False
        else:
            raise ValueError(f"unknown action '{action}'")

    async def _run_move(self) -> None:
        if self._fault == "never_complete":
            return
        while abs(self.target_position - self.position) > 1e-6:
            step = self.VELOCITY_UNITS_PER_SEC * self.STEP_S
            delta = self.target_position - self.position
            self.position += max(-step, min(step, delta))
            await self._clock.sleep(self.STEP_S)
        self.moving = False

    def is_action_complete(self, action: str, params: dict[str, Any]) -> bool:
        if action == "move":
            return not self.moving and abs(self.position - float(params["position"])) < 1e-6
        if action == "stop":
            return not self.moving
        return True

    def snapshot(self) -> TargetState:
        now = self._clock.now()
        return TargetState(
            target_id=self.target_id,
            fields={
                "position": ObservedValue(value=self.position, observed_at=now),
                "moving": ObservedValue(value=self.moving, observed_at=now),
                "homed": ObservedValue(value=self.homed, observed_at=now),
                "alarm": ObservedValue(value=self.alarm, observed_at=now),
            },
        )
