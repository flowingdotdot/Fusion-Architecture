"""Clock abstraction so timing (busy-timeout, session TTL, poll intervals) is testable
without real sleeps. ``fusion.simulation.fake_clock.FakeClock`` implements the same
Protocol with a virtual, test-driven timeline.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))
