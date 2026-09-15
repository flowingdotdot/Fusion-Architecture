"""Show state machine (doc section 11): IDLE/PREPARING/ARMED/RUNNING/HOLDING/
STOPPING/FAULTED, one active Show at a time. ``start()`` outside ARMED is rejected
outright -- the primary defense against re-entry from repeated button presses or
duplicate Trigger firings (doc: "반복 입력에도 Show 재진입·명령 중복 없음").

The actual "what does arming / running a Show mean" logic (talking to Runtime
clients, executing a Timeline) is injected as async callables rather than owned
here, so this state machine is testable without any real network client -- doc
section 11's transitions are the same regardless of how Cues actually get
submitted.

Per doc section 11, there is no Pause/Resume: HOLDING can only be followed by
``abort()`` (Abort-then-rearm is the standard operating model; a completed Show
returns straight to IDLE, there is no separate "DONE" state in the doc's list).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from enum import StrEnum


class ShowState(StrEnum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    HOLDING = "HOLDING"
    STOPPING = "STOPPING"
    FAULTED = "FAULTED"


OnStateChange = Callable[[ShowState], None]


class ShowController:
    def __init__(self, *, on_state_change: OnStateChange | None = None) -> None:
        self.state = ShowState.IDLE
        self.run_id: str | None = None
        self.last_error: str | None = None
        self._on_state_change = on_state_change
        self._run_task: asyncio.Task[None] | None = None
        self._hold_requested = False

    @property
    def hold_requested(self) -> bool:
        """Checked by whatever ``run`` callable is driving the Show (a Timeline
        executor) between Cues -- HOLD stops new Cue submission, it does not pretend
        to pause work already dispatched to a Runtime (doc section 11)."""
        return self._hold_requested

    def status(self) -> dict[str, str | None]:
        return {"state": self.state.value, "run_id": self.run_id, "last_error": self.last_error}

    def _set_state(self, state: ShowState) -> None:
        self.state = state
        if self._on_state_change:
            self._on_state_change(state)

    async def arm(self, prepare: Callable[[], Awaitable[None]]) -> None:
        if self.state != ShowState.IDLE:
            raise RuntimeError(f"cannot arm from state {self.state}")
        self._set_state(ShowState.PREPARING)
        try:
            await prepare()
        except Exception as exc:
            self.last_error = str(exc)
            self._set_state(ShowState.IDLE)
            raise
        self.last_error = None
        self._set_state(ShowState.ARMED)

    def start(self, run: Callable[[], Awaitable[None]], *, run_id: str) -> None:
        if self.state != ShowState.ARMED:
            raise RuntimeError(f"cannot start a Show from state {self.state}")
        self.run_id = run_id
        self.last_error = None
        self._hold_requested = False
        self._set_state(ShowState.RUNNING)
        self._run_task = asyncio.create_task(self._drive(run))

    async def _drive(self, run: Callable[[], Awaitable[None]]) -> None:
        try:
            await run()
            if self.state in (ShowState.RUNNING, ShowState.HOLDING):
                self._set_state(ShowState.IDLE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a Timeline/Cue failure faults the Show, it doesn't crash the process
            self.last_error = str(exc)
            self._set_state(ShowState.FAULTED)

    def hold(self) -> None:
        if self.state != ShowState.RUNNING:
            raise RuntimeError(f"cannot hold a Show from state {self.state}")
        self._hold_requested = True
        self._set_state(ShowState.HOLDING)

    async def abort(self, stop_all: Callable[[], Awaitable[None]]) -> None:
        if self.state not in (ShowState.RUNNING, ShowState.HOLDING, ShowState.FAULTED):
            raise RuntimeError(f"cannot abort a Show from state {self.state}")
        self._set_state(ShowState.STOPPING)
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task
        await stop_all()
        self._set_state(ShowState.IDLE)
