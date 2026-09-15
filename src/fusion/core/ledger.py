"""Command ledger (doc section 7): request_id dedup and conflict detection.

Same request_id + same content -> the existing command_id, no re-execution.
Same request_id + different content -> REQUEST_ID_CONFLICT. Rejections are recorded
too (a request_id always maps to exactly one outcome once resolved).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fusion.contracts.command import CommandRecord, CommandStatus, CommandSubmitRequest, fingerprint
from fusion.contracts.errors import ErrorCode, FusionError
from fusion.contracts.ids import new_command_id


@dataclass
class _Entry:
    fingerprint: tuple[object, ...]
    command_id: str


class CommandLedger:
    def __init__(self) -> None:
        self._by_request_id: dict[str, _Entry] = {}
        self._by_command_id: dict[str, CommandRecord] = {}

    def begin(
        self,
        req: CommandSubmitRequest,
        *,
        runtime_id: str,
        runtime_boot_id: str,
        execution_generation: int,
    ) -> tuple[CommandRecord, bool]:
        """Returns (record, is_new). Raises FusionError(REQUEST_ID_CONFLICT) if
        ``req.request_id`` was already used for different content."""
        fp = fingerprint(req)
        existing = self._by_request_id.get(req.request_id)
        if existing is not None:
            if existing.fingerprint != fp:
                raise FusionError(
                    ErrorCode.REQUEST_ID_CONFLICT,
                    f"request_id '{req.request_id}' already used with different content",
                )
            return self._by_command_id[existing.command_id], False

        command_id = new_command_id()
        now = time.time()
        record = CommandRecord(
            command_id=command_id,
            request_id=req.request_id,
            runtime_id=runtime_id,
            runtime_boot_id=runtime_boot_id,
            target_id=req.target_id,
            action=req.action,
            params=req.params,
            completion_requirement=req.completion_requirement,
            status=CommandStatus.QUEUED,
            execution_generation=execution_generation,
            run_id=req.run_id,
            cue_id=req.cue_id,
            source=req.source,
            actor=req.actor,
            created_at=now,
            updated_at=now,
        )
        self._by_request_id[req.request_id] = _Entry(fingerprint=fp, command_id=command_id)
        self._by_command_id[command_id] = record
        return record, True

    def get(self, command_id: str) -> CommandRecord | None:
        return self._by_command_id.get(command_id)
