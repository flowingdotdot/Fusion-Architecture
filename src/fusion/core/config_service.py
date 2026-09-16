"""Config Stage/Apply service (doc section 15): "Upload -> Validate -> Stage ->
Apply -> Arm -> Start를 분리한다." This module owns Stage (always allowed, doc:
"실행 중에도 Stage는 허용한다") and Apply (gated by a caller-supplied guard, since
what "safe to apply right now" means is Runtime-specific -- a Motor Runtime cares
about busy Targets, a Scheduler cares about Show state).

A failed/rejected Apply never advances ``active_revision_id`` and never raises out
of ``apply()`` -- the doc explicitly treats "적용 불가" as an expected outcome to
report, not an exceptional one (doc: "적용 불가 이유를 반환한다"), and a real
exception during the guard or the apply action itself becomes a FAILED
``ApplyRecord`` rather than propagating and leaving no journal entry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fusion.contracts.config import ApplyOutcome, ApplyRecord, ConfigRevision
from fusion.contracts.errors import ErrorCode, FusionError
from fusion.contracts.ids import new_id
from fusion.core.clock import Clock

DEFAULT_JOURNAL_LIMIT = 50


def _content_hash(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class ConfigService:
    def __init__(self, clock: Clock, *, journal_limit: int = DEFAULT_JOURNAL_LIMIT) -> None:
        self._clock = clock
        self._journal_limit = journal_limit
        self._staged: dict[str, ConfigRevision] = {}
        self._active_revision_id: str | None = None
        self._journal: list[ApplyRecord] = []
        self._apply_lock = asyncio.Lock()

    @property
    def active_revision_id(self) -> str | None:
        return self._active_revision_id

    def stage(self, kind: str, content: dict[str, Any]) -> ConfigRevision:
        revision = ConfigRevision(
            revision_id=new_id("cfgrev"),
            kind=kind,
            content_hash=_content_hash(content),
            content=content,
            staged_at=self._clock.now(),
        )
        self._staged[revision.revision_id] = revision
        return revision

    def get_staged(self, revision_id: str) -> ConfigRevision | None:
        return self._staged.get(revision_id)

    async def apply(
        self,
        revision_id: str,
        *,
        expected_active_revision: str | None,
        apply_guard: Callable[[], Awaitable[None]],
        on_applied: Callable[[], None] | None = None,
    ) -> ApplyRecord:
        """``apply_guard`` raises ``FusionError`` (any code) if applying isn't
        currently allowed; that becomes a REJECTED record, not an exception out of
        this method. ``on_applied`` runs only on success, while still holding the
        apply lock -- the doc's "출력 게이트 차단, execution_generation 갱신,
        구명령 무효화" step, owned by whoever constructs this service since it's
        Runtime-specific."""
        if revision_id not in self._staged:
            raise FusionError(
                ErrorCode.VALIDATION_ERROR, f"unknown staged revision '{revision_id}'"
            )

        async with self._apply_lock:
            if (
                expected_active_revision is not None
                and expected_active_revision != self._active_revision_id
            ):
                return self._record(
                    revision_id,
                    ApplyOutcome.REJECTED,
                    f"expected active revision '{expected_active_revision}', "
                    f"actual is '{self._active_revision_id}'",
                )
            try:
                await apply_guard()
            except FusionError as exc:
                return self._record(revision_id, ApplyOutcome.REJECTED, exc.message)
            except Exception as exc:  # noqa: BLE001 - a guard/apply-step crash is a FAILED record, not a dropped exception
                return self._record(revision_id, ApplyOutcome.FAILED, str(exc))

            self._active_revision_id = revision_id
            if on_applied is not None:
                on_applied()
            return self._record(revision_id, ApplyOutcome.SUCCEEDED, None)

    def _record(self, revision_id: str, outcome: ApplyOutcome, detail: str | None) -> ApplyRecord:
        record = ApplyRecord(
            revision_id=revision_id, outcome=outcome, detail=detail, applied_at=self._clock.now()
        )
        self._journal.append(record)
        if len(self._journal) > self._journal_limit:
            del self._journal[: len(self._journal) - self._journal_limit]
        return record

    def status(self) -> dict[str, Any]:
        return {
            "staged_revision_ids": list(self._staged.keys()),
            "active_revision_id": self._active_revision_id,
            "journal": [r.model_dump() for r in self._journal[-10:]],
        }
