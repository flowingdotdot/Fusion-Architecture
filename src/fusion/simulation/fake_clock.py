"""Deterministic virtual clock for tests (doc section 18). Time only moves when a
test calls ``advance()`` -- no wall-clock sleeps, so timeout/expiry paths are fast
and reproducible.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class _Waiter:
    wake_at: float
    event: asyncio.Event = field(default_factory=asyncio.Event)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._waiters: list[_Waiter] = []

    def now(self) -> float:
        return self._now

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        waiter = _Waiter(wake_at=self._now + seconds)
        self._waiters.append(waiter)
        await waiter.event.wait()

    async def advance(self, seconds: float) -> None:
        """Moves virtual time forward by ``seconds``, waking any waiter whose
        deadline falls at or before the new time, strictly in deadline order, giving
        the event loop a turn after each wake so a re-armed sleep is picked up.

        Always yields once up front: a task created just before ``advance()`` is
        called (e.g. via ``asyncio.create_task``) has not run at all yet and so has
        not registered its first waiter -- without this yield, ``self._waiters``
        would look empty and ``advance`` would fast-forward time without ever
        waking it.
        """
        target = self._now + seconds
        await asyncio.sleep(0)
        while self._waiters:
            earliest = min(self._waiters, key=lambda w: w.wake_at)
            if earliest.wake_at > target:
                break
            self._waiters.remove(earliest)
            self._now = earliest.wake_at
            earliest.event.set()
            await asyncio.sleep(0)
        self._now = target
