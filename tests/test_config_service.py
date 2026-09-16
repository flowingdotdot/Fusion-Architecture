"""ConfigService (doc section 15): Stage is always allowed; Apply is gated by a
caller-supplied guard and never raises for an expected "can't apply right now."
"""

from __future__ import annotations

from fusion.contracts.config import ApplyOutcome
from fusion.contracts.errors import ErrorCode, FusionError
from fusion.core.config_service import ConfigService
from fusion.simulation.fake_clock import FakeClock


async def _always_allow() -> None:
    return None


async def _always_reject() -> None:
    raise FusionError(ErrorCode.VALIDATION_ERROR, "not right now")


async def test_stage_returns_a_hashed_immutable_revision() -> None:
    service = ConfigService(FakeClock())
    revision = service.stage("project", {"a": 1})
    assert revision.kind == "project"
    assert revision.content == {"a": 1}
    assert len(revision.content_hash) > 0
    assert service.get_staged(revision.revision_id) is revision


async def test_successful_apply_advances_active_revision_and_runs_on_applied() -> None:
    service = ConfigService(FakeClock())
    revision = service.stage("project", {"a": 1})
    applied_calls = []

    record = await service.apply(
        revision.revision_id,
        expected_active_revision=None,
        apply_guard=_always_allow,
        on_applied=lambda: applied_calls.append(1),
    )
    assert record.outcome == ApplyOutcome.SUCCEEDED
    assert service.active_revision_id == revision.revision_id
    assert applied_calls == [1]


async def test_guard_rejection_does_not_advance_active_revision() -> None:
    service = ConfigService(FakeClock())
    revision = service.stage("project", {"a": 1})

    record = await service.apply(
        revision.revision_id, expected_active_revision=None, apply_guard=_always_reject
    )
    assert record.outcome == ApplyOutcome.REJECTED
    assert service.active_revision_id is None  # staged revision is preserved, nothing changed
    assert service.get_staged(revision.revision_id) is not None


async def test_unexpected_guard_exception_becomes_failed_not_propagated() -> None:
    service = ConfigService(FakeClock())
    revision = service.stage("project", {"a": 1})

    async def _boom() -> None:
        raise RuntimeError("kaboom")

    record = await service.apply(
        revision.revision_id, expected_active_revision=None, apply_guard=_boom
    )
    assert record.outcome == ApplyOutcome.FAILED
    assert service.active_revision_id is None


async def test_expected_active_revision_mismatch_is_rejected() -> None:
    service = ConfigService(FakeClock())
    first = service.stage("project", {"a": 1})
    await service.apply(first.revision_id, expected_active_revision=None, apply_guard=_always_allow)

    second = service.stage("project", {"a": 2})
    record = await service.apply(
        second.revision_id, expected_active_revision="wrong-id", apply_guard=_always_allow
    )
    assert record.outcome == ApplyOutcome.REJECTED
    assert service.active_revision_id == first.revision_id  # unchanged


async def test_applying_an_unknown_revision_id_raises() -> None:
    service = ConfigService(FakeClock())
    try:
        await service.apply(
            "does-not-exist", expected_active_revision=None, apply_guard=_always_allow
        )
        raise AssertionError("expected a FusionError")
    except FusionError as exc:
        assert exc.code == ErrorCode.VALIDATION_ERROR
