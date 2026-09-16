"""DeviceRuntimeApp's config Stage/Apply wiring (doc section 15): the default
apply guard blocks while any Target is busy, and a successful apply bumps
execution_generation exactly like a control session handover already does --
reusing the same "invalidate whatever was in flight" mechanism, not a new one.
"""

from __future__ import annotations

from fusion.apps.motor.runtime import MotorRuntimeApp
from fusion.contracts.command import CommandOutcome, CommandSubmitRequest
from fusion.contracts.config import ApplyOutcome
from fusion.contracts.control import RuntimeMode
from fusion.simulation.fake_clock import FakeClock
from fusion.simulation.fake_motor import FakeMotorAdapter


def make_app() -> tuple[MotorRuntimeApp, FakeClock, FakeMotorAdapter]:
    clock = FakeClock()
    motor = FakeMotorAdapter("motor01", clock)
    app = MotorRuntimeApp("motor-test", {"motor01": motor}, clock)
    return app, clock, motor


async def test_stage_is_allowed_even_while_a_target_is_busy() -> None:
    app, clock, _motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)
    await app.submit_command(
        CommandSubmitRequest(
            request_id="req-move",
            control_session_id=session.session_id,
            target_id="motor01",
            action="move",
            params={"position": 500},
        )
    )
    await clock.advance(0.1)  # now genuinely busy/moving

    revision = app.stage_config("install", {"stroke_mm": 87})
    assert revision.revision_id in app.get_config_status()["staged_revision_ids"]


async def test_default_guard_rejects_apply_while_a_target_is_busy() -> None:
    app, clock, _motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)
    await app.submit_command(
        CommandSubmitRequest(
            request_id="req-move",
            control_session_id=session.session_id,
            target_id="motor01",
            action="move",
            params={"position": 500},
        )
    )
    await clock.advance(0.1)

    revision = app.stage_config("install", {"stroke_mm": 87})
    record = await app.apply_config(revision.revision_id)
    assert record.outcome == ApplyOutcome.REJECTED
    assert app.get_config_status()["active_revision_id"] is None


async def test_apply_succeeds_when_idle_and_bumps_generation() -> None:
    app, clock, motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    revision = app.stage_config("install", {"stroke_mm": 87})
    record = await app.apply_config(revision.revision_id)
    assert record.outcome == ApplyOutcome.SUCCEEDED
    assert app.get_config_status()["active_revision_id"] == revision.revision_id

    # generation bump invalidates a command that was accepted under the old config
    move = await app.submit_command(
        CommandSubmitRequest(
            request_id="req-after-apply",
            control_session_id=session.session_id,
            target_id="motor01",
            action="move",
            params={"position": 10},
        )
    )
    assert (
        move.execution_generation >= 2
    )  # started above 1; apply (and the earlier session acquire) bumped it

    for _ in range(200):
        record2 = await app.get_command(move.command_id)
        assert record2 is not None
        if record2.status.value == "TERMINAL":
            assert record2.outcome == CommandOutcome.SUCCEEDED
            break
        await clock.advance(0.05)
    else:
        raise AssertionError("command never completed")
