"""Generic per-Target Command/Event/ControlSession Runtime (doc section 3-9):
everything MotorRuntimeApp needed turned out to be zero motor-specific -- Target
Registry, Command Ledger, per-Target execution, control sessions, and the Event
bus are exactly the same shape doc section 6's API table describes for Motor
*and* Video *and* Setting. Extracted here once Video became a second real
consumer (not built ahead of need -- LED still isn't, and still doesn't get this).

``publish_event`` lets an adapter emit a fact that isn't tied to any specific
command's lifecycle -- e.g. Video's "playback.ended" firing on its own once a
video finishes playing, independent of whichever command started it (doc section
12: "play 명령의 성공과 playback.ended는 구분한다").
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from fusion.contracts.command import (
    CommandOutcome,
    CommandRecord,
    CommandStatus,
    CommandSubmitRequest,
)
from fusion.contracts.config import ApplyRecord, ConfigRevision
from fusion.contracts.control import ControlSession, RuntimeMode
from fusion.contracts.errors import ErrorCode, FusionError
from fusion.contracts.event import Event
from fusion.contracts.ids import new_boot_id
from fusion.contracts.plugin import TargetManifest
from fusion.core.blob_store import BlobHashMismatchError, BlobRef, BlobStore, BlobTooLargeError
from fusion.core.clock import Clock
from fusion.core.config_service import ConfigService
from fusion.core.control_session import ControlSessionManager
from fusion.core.event_bus import EventBus
from fusion.core.ledger import CommandLedger
from fusion.core.target_runner import TargetRunner
from fusion.plugin_sdk.base import TargetAdapter, validate_manifest

PROTOCOL_VERSION = 1

ApplyGuard = Callable[[], Awaitable[None]]


class DeviceRuntimeApp:
    def __init__(
        self,
        runtime_id: str,
        adapters: Mapping[str, TargetAdapter],
        clock: Clock,
        *,
        app_name: str,
        apply_guard: ApplyGuard | None = None,
        blob_store: BlobStore | None = None,
    ) -> None:
        for adapter in adapters.values():
            validate_manifest(adapter)

        self.runtime_id = runtime_id
        self.runtime_boot_id = new_boot_id()
        self.app_name = app_name
        self._clock = clock
        self._adapters = adapters
        self._ledger = CommandLedger()
        self._events = EventBus(runtime_id, self.runtime_boot_id)
        self._sessions = ControlSessionManager(clock, self.runtime_boot_id)
        self._config = ConfigService(clock)
        self._apply_guard = apply_guard or self._default_apply_guard
        self._blobs = blob_store or BlobStore(Path("var") / "fusion" / runtime_id / "blobs")
        self._logger = logging.getLogger(f"fusion.{app_name}")
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
            "app": self.app_name,
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
            self._logger.info(
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
                self._logger.warning(
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
            self._logger.info(
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

    def publish_event(
        self,
        event_type: str,
        *,
        target_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> Event:
        """For an adapter to report a fact of its own that isn't a command result
        (e.g. Video's "playback.ended"). Not part of ``plugin_sdk.TargetAdapter`` --
        adapters that need it are handed this method explicitly by whoever
        assembles the Runtime (see ``apps/video/main.py``), keeping the base
        adapter interface free of Runtime-plumbing concerns."""
        return self._events.publish(event_type, target_id=target_id, payload=payload)

    # ---- control sessions ----

    async def acquire_control_session(
        self, actor: str, mode: RuntimeMode, ttl_s: float | None = None
    ) -> ControlSession:
        for runner in self._runners.values():
            runner.bump_generation()
        session = self._sessions.acquire(actor, mode, ttl_s=ttl_s)
        self._logger.info(
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
        self._logger.info("control session released session_id=%s", session_id)

    # ---- config stage/apply (doc section 15) ----

    async def _default_apply_guard(self) -> None:
        """No app-specific guard was supplied: the only generic, always-correct
        check is "nothing is actively in flight on this Runtime right now" -- a
        real Motor/Video app should normally supply its own (e.g. Scheduler checks
        Show state instead), but this default still enforces *something* rather
        than silently allowing Apply during an active command."""
        busy_targets = [target_id for target_id, runner in self._runners.items() if runner.busy]
        if busy_targets:
            raise FusionError(
                ErrorCode.VALIDATION_ERROR,
                f"targets busy, cannot apply now: {', '.join(busy_targets)}",
            )

    def stage_config(self, kind: str, content: dict[str, Any]) -> ConfigRevision:
        revision = self._config.stage(kind, content)
        self._logger.info("config staged revision_id=%s kind=%s", revision.revision_id, kind)
        return revision

    async def apply_config(
        self, revision_id: str, expected_active_revision: str | None = None
    ) -> ApplyRecord:
        record = await self._config.apply(
            revision_id,
            expected_active_revision=expected_active_revision,
            apply_guard=self._apply_guard,
            on_applied=self._on_config_applied,
        )
        self._logger.info(
            "config apply revision_id=%s outcome=%s", revision_id, record.outcome.value
        )
        return record

    def _on_config_applied(self) -> None:
        # doc: "출력 게이트 차단, execution_generation 갱신, 구명령 무효화 후 새
        # 구성을 적용한다" -- reuses the exact same generation-bump mechanism a
        # control session handover or a STOP-priority action already uses to
        # invalidate whatever was in flight under the old configuration.
        for runner in self._runners.values():
            runner.bump_generation()

    def get_config_status(self) -> dict[str, Any]:
        return self._config.status()

    # ---- large file transfer (doc section 14: "대용량 패키지·영상·대량 로그는
    # HTTPS로 전송한다") ----

    async def save_blob(
        self, chunks: AsyncIterator[bytes], *, expected_sha256: str | None = None
    ) -> BlobRef:
        try:
            ref = await self._blobs.save_stream(chunks, expected_sha256=expected_sha256)
        except (BlobTooLargeError, BlobHashMismatchError) as exc:
            raise FusionError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
        self._logger.info("blob stored sha256=%s size=%d", ref.sha256, ref.size)
        return ref

    def open_blob(self, sha256: str) -> Path:
        if not self._blobs.exists(sha256):
            raise FusionError(ErrorCode.BLOB_NOT_FOUND, f"unknown blob sha256 '{sha256}'")
        return self._blobs.path_for(sha256)
