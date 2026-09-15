"""Headless Motor Runtime (doc section 3-9, stage 1-2): owns Target Registry,
Command Ledger, per-Target execution, control sessions and the Event bus. No PySide6
import anywhere in this module -- a Studio UI (stage 3) would be a separate process
talking to this over the transport layer, never importing this module directly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from fusion.contracts.command import (
    CommandOutcome,
    CommandRecord,
    CommandStatus,
    CommandSubmitRequest,
)
from fusion.contracts.control import ControlSession, RuntimeMode
from fusion.contracts.errors import ErrorCode, FusionError
from fusion.contracts.event import Event
from fusion.contracts.ids import new_boot_id
from fusion.contracts.plugin import TargetManifest
from fusion.core.clock import Clock
from fusion.core.control_session import ControlSessionManager
from fusion.core.event_bus import EventBus
from fusion.core.ledger import CommandLedger
from fusion.core.target_runner import TargetRunner
from fusion.plugin_sdk.base import TargetAdapter, validate_manifest

PROTOCOL_VERSION = 1

logger = logging.getLogger("fusion.motor")


class MotorRuntimeApp:
    def __init__(
        self, runtime_id: str, adapters: Mapping[str, TargetAdapter], clock: Clock
    ) -> None:
        for adapter in adapters.values():
            validate_manifest(adapter)

        self.runtime_id = runtime_id
        self.runtime_boot_id = new_boot_id()
        self._clock = clock
        self._adapters = adapters
        self._ledger = CommandLedger()
        self._events = EventBus(runtime_id, self.runtime_boot_id)
        self._sessions = ControlSessionManager(clock, self.runtime_boot_id)
        self._runners: dict[str, TargetRunner] = {
            target_id: TargetRunner(target_id, adapter, clock, on_change=self._on_command_change)
            for target_id, adapter in adapters.items()
        }

    @property
    def sequence(self) -> int:
        return self._events.sequence

    # ---- info / targets / snapshot ----

    async def get_info(self) -> dict[str, object]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "runtime_id": self.runtime_id,
            "runtime_boot_id": self.runtime_boot_id,
            "app": "fusion-motor",
        }

    async def get_targets(self) -> list[TargetManifest]:
        return [adapter.manifest() for adapter in self._adapters.values()]

    async def get_snapshot(self) -> dict[str, object]:
        return {
            "sequence": self._events.sequence,
            "targets": {
                target_id: adapter.snapshot().model_dump()
                for target_id, adapter in self._adapters.items()
            },
        }

    # ---- commands ----

    async def submit_command(self, req: CommandSubmitRequest) -> CommandRecord:
        if req.target_id not in self._runners:
            raise FusionError(ErrorCode.UNKNOWN_TARGET, f"unknown target '{req.target_id}'")

        self._sessions.validate(req.control_session_id)
        runner = self._runners[req.target_id]

        record, is_new = self._ledger.begin(
            req,
            runtime_id=self.runtime_id,
            runtime_boot_id=self.runtime_boot_id,
            execution_generation=runner.execution_generation,
        )
        if is_new:
            logger.info(
                "command accepted request_id=%s command_id=%s target_id=%s action=%s cue_id=%s",
                req.request_id,
                record.command_id,
                req.target_id,
                req.action,
                req.cue_id,
            )
            try:
                await runner.submit(record)
            except FusionError as exc:
                logger.warning(
                    "command rejected command_id=%s target_id=%s action=%s code=%s: %s",
                    record.command_id,
                    req.target_id,
                    req.action,
                    exc.code.value,
                    exc.message,
                )
                record.status = CommandStatus.TERMINAL
                record.outcome = CommandOutcome.REJECTED
                record.error = exc.to_dict()
                self._on_command_change(record)
                raise
        return record

    async def get_command(self, command_id: str) -> CommandRecord | None:
        return self._ledger.get(command_id)

    def _on_command_change(self, record: CommandRecord) -> None:
        if record.status == CommandStatus.TERMINAL:
            logger.info(
                "command terminal command_id=%s target_id=%s outcome=%s achieved_completion=%s",
                record.command_id,
                record.target_id,
                record.outcome,
                record.achieved_completion,
            )
            self._events.publish(
                "command.completed",
                target_id=record.target_id,
                command_id=record.command_id,
                run_id=record.run_id,
                cue_id=record.cue_id,
                payload={
                    "outcome": record.outcome.value if record.outcome else None,
                    "achieved_completion": record.achieved_completion.value
                    if record.achieved_completion
                    else None,
                },
            )
        else:
            self._events.publish(
                "command.status_changed",
                target_id=record.target_id,
                command_id=record.command_id,
                run_id=record.run_id,
                cue_id=record.cue_id,
                payload={"status": record.status.value},
            )

    # ---- events ----

    def replay_since(self, after_sequence: int) -> list[Event] | None:
        return self._events.replay_since(after_sequence)

    def subscribe_events(self) -> tuple[int, asyncio.Queue[Event | None]]:
        return self._events.subscribe()

    def unsubscribe_events(self, sub_id: int) -> None:
        self._events.unsubscribe(sub_id)

    # ---- control sessions ----

    async def acquire_control_session(
        self, actor: str, mode: RuntimeMode, ttl_s: float | None = None
    ) -> ControlSession:
        for runner in self._runners.values():
            runner.bump_generation()
        session = self._sessions.acquire(actor, mode, ttl_s=ttl_s)
        logger.info(
            "control session acquired session_id=%s actor=%s mode=%s",
            session.session_id,
            actor,
            mode.value,
        )
        return session

    async def renew_control_session(self, session_id: str) -> ControlSession:
        return self._sessions.renew(session_id)

    async def release_control_session(self, session_id: str) -> None:
        self._sessions.release(session_id)
        for runner in self._runners.values():
            runner.bump_generation()
        logger.info("control session released session_id=%s", session_id)
