"""Fake input source (doc section 11/19 stage 3: "Fake button/sensor input, no real
hardware manual exists"). A real OSC/UDP transport turning wire messages into
``InputEvent``s is stage 6 scope -- this exists purely to drive/test the
TriggerEngine without one.
"""

from __future__ import annotations

from fusion.contracts.trigger import InputEvent
from fusion.core.clock import Clock
from fusion.core.trigger_engine import TriggerEngine


class FakeInputSource:
    def __init__(self, engine: TriggerEngine, clock: Clock, source: str = "fake-panel") -> None:
        self._engine = engine
        self._clock = clock
        self._source = source

    async def press(self, button_type: str, value: float | str | bool = True) -> None:
        await self._engine.handle(
            InputEvent(
                source=self._source, type=button_type, value=value, occurred_at=self._clock.now()
            )
        )
