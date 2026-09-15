"""Target runner (doc section 9-10): serializes execution for one Target.

One active command at a time. A "normal" priority action is rejected outright (BUSY)
while another is in flight; a "control" priority action (stop) is allowed through
regardless. ``execution_generation`` is stamped on accept and re-checked at each
lifecycle step -- if it changes underneath an in-flight command (Apply, control
session handover, STOP-triggered invalidation), the command is cancelled rather than
left to silently succeed or hang.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from fusion.contracts.command import (
    CommandOutcome,
    CommandRecord,
    CommandStatus,
    CompletionRequirement,
)
from fusion.contracts.errors import ErrorCode, FusionError
from fusion.core.clock import Clock
from fusion.plugin_sdk.base import TargetAdapter

OnChange = Callable[[CommandRecord], None]


class TargetRunner:
    POLL_INTERVAL_S = 0.02
    OBSERVE_TIMEOUT_S = 10.0

    def __init__(
        self,
        target_id: str,
        adapter: TargetAdapter,
        clock: Clock,
        *,
        on_change: OnChange | None = None,
    ) -> None:
        self.target_id = target_id
        self._adapter = adapter
        self._clock = clock
        self._on_change = on_change
        self._active: CommandRecord | None = None
        self.execution_generation = 1

    @property
    def busy(self) -> bool:
        return self._active is not None and self._active.status != CommandStatus.TERMINAL

    def bump_generation(self) -> None:
        self.execution_generation += 1

    async def submit(self, record: CommandRecord) -> None:
        """Validates, then drives the command through DISPATCHING/DISPATCHED/
        ACKNOWLEDGED synchronously (so ``adapter.execute`` has genuinely run, and any
        requested completion_requirement up to "acknowledged" is already resolved, by
        the time this returns). Only the open-ended OBSERVING poll -- waiting for a
        physical/semantic completion that has no fixed bound -- is backgrounded.
        Raises FusionError for anything rejected before ``execute`` is called."""
        manifest = self._adapter.manifest()
        action_spec = manifest.get_action(record.action)
        if action_spec is None:
            raise FusionError(
                ErrorCode.UNKNOWN_ACTION,
                f"unknown action '{record.action}' for target '{self.target_id}'",
            )

        is_control = action_spec.priority == "control"
        if self.busy and not is_control:
            raise FusionError(ErrorCode.BUSY, f"target '{self.target_id}' is busy")
        if self.busy and is_control:
            # Preempting an in-flight command (e.g. stop): bump generation so the
            # command being preempted notices the mismatch and cancels itself,
            # instead of racing the new command or hanging until its own timeout.
            self.bump_generation()

        record.execution_generation = self.execution_generation
        self._active = record
        generation_at_submit = record.execution_generation

        try:
            record.status = CommandStatus.DISPATCHING
            self._touch(record)

            await self._adapter.execute(record.action, record.params)
            record.status = CommandStatus.DISPATCHED
            self._touch(record)
            if record.completion_requirement == CompletionRequirement.DISPATCHED:
                self._finish(record, CommandOutcome.SUCCEEDED, CompletionRequirement.DISPATCHED)
                return

            record.status = CommandStatus.ACKNOWLEDGED
            self._touch(record)
            if record.completion_requirement == CompletionRequirement.ACKNOWLEDGED:
                self._finish(record, CommandOutcome.SUCCEEDED, CompletionRequirement.ACKNOWLEDGED)
                return

            record.status = CommandStatus.OBSERVING
            self._touch(record)
        except asyncio.CancelledError:
            self._finish(record, CommandOutcome.CANCELLED, None)
            raise
        except Exception as exc:  # noqa: BLE001 - adapter faults become a FAILED outcome, not a crash
            record.error = {"code": ErrorCode.INTERNAL_ERROR.value, "message": str(exc)}
            self._finish(record, CommandOutcome.FAILED, None)
            return

        asyncio.create_task(self._observe(record, generation_at_submit))

    async def _observe(self, record: CommandRecord, generation_at_submit: int) -> None:
        try:
            deadline = self._clock.now() + self.OBSERVE_TIMEOUT_S
            while self._clock.now() < deadline:
                if generation_at_submit != self.execution_generation:
                    self._finish(record, CommandOutcome.CANCELLED, None)
                    return
                if self._adapter.is_action_complete(record.action, record.params):
                    self._finish(record, CommandOutcome.SUCCEEDED, CompletionRequirement.OBSERVED)
                    return
                await self._clock.sleep(self.POLL_INTERVAL_S)
            self._finish(record, CommandOutcome.EXPIRED, None)
        except asyncio.CancelledError:
            self._finish(record, CommandOutcome.CANCELLED, None)
            raise
        except Exception as exc:  # noqa: BLE001 - adapter faults become a FAILED outcome, not a crash
            record.error = {"code": ErrorCode.INTERNAL_ERROR.value, "message": str(exc)}
            self._finish(record, CommandOutcome.FAILED, None)

    def _touch(self, record: CommandRecord) -> None:
        record.updated_at = time.time()
        if self._on_change:
            self._on_change(record)

    def _finish(
        self, record: CommandRecord, outcome: CommandOutcome, achieved: CompletionRequirement | None
    ) -> None:
        record.status = CommandStatus.TERMINAL
        record.outcome = outcome
        record.achieved_completion = achieved
        self._touch(record)
        if self._active is record:
            self._active = None
