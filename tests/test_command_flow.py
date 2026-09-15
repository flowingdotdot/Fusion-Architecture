"""Stage 1-2 core scenarios (doc section 18 validation table), driven headlessly
against MotorRuntimeApp with a FakeClock + FakeMotorAdapter -- no HTTP involved.
"""

from __future__ import annotations

import pytest

from fusion.apps.motor.runtime import MotorRuntimeApp
from fusion.contracts.command import CommandOutcome, CommandRecord, CommandSubmitRequest
from fusion.contracts.control import RuntimeMode
from fusion.contracts.errors import ErrorCode, FusionError
from fusion.simulation.fake_clock import FakeClock
from fusion.simulation.fake_motor import FakeMotorAdapter


def make_app(**motor_kwargs: object) -> tuple[MotorRuntimeApp, FakeClock, FakeMotorAdapter]:
    clock = FakeClock()
    motor = FakeMotorAdapter("motor01", clock, **motor_kwargs)  # type: ignore[arg-type]
    app = MotorRuntimeApp("motor-test", {"motor01": motor}, clock)
    return app, clock, motor


async def run_to_completion(
    app: MotorRuntimeApp, clock: FakeClock, command_id: str, *, timeout_ticks: int = 1000
) -> CommandRecord:
    for _ in range(timeout_ticks):
        record = await app.get_command(command_id)
        assert record is not None
        if record.status.value == "TERMINAL":
            return record
        await clock.advance(0.05)
    raise AssertionError("command did not reach a terminal state in time")


async def test_duplicate_request_id_executes_once() -> None:
    app, clock, motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    req = CommandSubmitRequest(
        request_id="req-1",
        control_session_id=session.session_id,
        target_id="motor01",
        action="move",
        params={"position": 100},
    )
    r1 = await app.submit_command(req)
    r2 = await app.submit_command(req)
    assert r1.command_id == r2.command_id
    assert motor.move_count == 1

    final = await run_to_completion(app, clock, r1.command_id)
    assert final.outcome == CommandOutcome.SUCCEEDED
    assert final.achieved_completion is not None
    assert motor.position == 100


async def test_conflicting_request_id_is_rejected() -> None:
    app, clock, motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    req1 = CommandSubmitRequest(
        request_id="req-2",
        control_session_id=session.session_id,
        target_id="motor01",
        action="move",
        params={"position": 10},
    )
    r1 = await app.submit_command(req1)

    req2 = CommandSubmitRequest(
        request_id="req-2",
        control_session_id=session.session_id,
        target_id="motor01",
        action="move",
        params={"position": 999},
    )
    with pytest.raises(FusionError) as excinfo:
        await app.submit_command(req2)
    assert excinfo.value.code == ErrorCode.REQUEST_ID_CONFLICT

    await run_to_completion(app, clock, r1.command_id)


async def test_busy_target_rejects_second_normal_command() -> None:
    app, clock, motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    req1 = CommandSubmitRequest(
        request_id="req-a",
        control_session_id=session.session_id,
        target_id="motor01",
        action="move",
        params={"position": 500},
    )
    r1 = await app.submit_command(req1)

    req2 = CommandSubmitRequest(
        request_id="req-b",
        control_session_id=session.session_id,
        target_id="motor01",
        action="move",
        params={"position": 10},
    )
    with pytest.raises(FusionError) as excinfo:
        await app.submit_command(req2)
    assert excinfo.value.code == ErrorCode.BUSY

    await run_to_completion(app, clock, r1.command_id)


async def test_stop_is_allowed_through_while_busy() -> None:
    app, clock, motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    move_req = CommandSubmitRequest(
        request_id="req-move",
        control_session_id=session.session_id,
        target_id="motor01",
        action="move",
        params={"position": 500},
    )
    move_record = await app.submit_command(move_req)
    await clock.advance(0.1)
    assert motor.moving is True

    stop_req = CommandSubmitRequest(
        request_id="req-stop",
        control_session_id=session.session_id,
        target_id="motor01",
        action="stop",
        completion_requirement="acknowledged",
    )
    stop_record = await app.submit_command(stop_req)

    final_stop = await run_to_completion(app, clock, stop_record.command_id)
    assert final_stop.outcome == CommandOutcome.SUCCEEDED
    assert motor.moving is False

    final_move = await run_to_completion(app, clock, move_record.command_id)
    assert final_move.outcome == CommandOutcome.CANCELLED


async def test_command_requires_a_valid_control_session() -> None:
    app, _clock, _motor = make_app()
    req = CommandSubmitRequest(
        request_id="req-x",
        control_session_id="does-not-exist",
        target_id="motor01",
        action="move",
        params={"position": 1},
    )
    with pytest.raises(FusionError) as excinfo:
        await app.submit_command(req)
    assert excinfo.value.code == ErrorCode.CONTROL_SESSION_INVALID


async def test_control_session_expires() -> None:
    app, clock, _motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL, ttl_s=1.0)
    await clock.advance(2.0)

    req = CommandSubmitRequest(
        request_id="req-y",
        control_session_id=session.session_id,
        target_id="motor01",
        action="move",
        params={"position": 1},
    )
    with pytest.raises(FusionError) as excinfo:
        await app.submit_command(req)
    assert excinfo.value.code == ErrorCode.CONTROL_SESSION_EXPIRED


async def test_restart_invalidates_a_previous_runtimes_session() -> None:
    app1, _clock1, _motor1 = make_app()
    session = await app1.acquire_control_session("tester", RuntimeMode.MANUAL)

    app2, _clock2, _motor2 = make_app()  # simulates the Motor process restarting (new boot id)
    req = CommandSubmitRequest(
        request_id="req-z",
        control_session_id=session.session_id,
        target_id="motor01",
        action="move",
        params={"position": 1},
    )
    with pytest.raises(FusionError) as excinfo:
        await app2.submit_command(req)
    assert excinfo.value.code == ErrorCode.CONTROL_SESSION_INVALID


async def test_new_control_session_cancels_in_flight_command() -> None:
    app, clock, motor = make_app()
    session1 = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    req = CommandSubmitRequest(
        request_id="req-c1",
        control_session_id=session1.session_id,
        target_id="motor01",
        action="move",
        params={"position": 1000},
    )
    record = await app.submit_command(req)

    await clock.advance(0.1)  # move is under way but nowhere near position 1000 yet
    assert motor.moving is True

    await app.acquire_control_session(
        "tester2", RuntimeMode.SHOW
    )  # control handover bumps execution_generation

    final = await run_to_completion(app, clock, record.command_id)
    assert final.outcome == CommandOutcome.CANCELLED


async def test_action_that_never_completes_expires_rather_than_hanging() -> None:
    app, clock, _motor = make_app(fault="never_complete")
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    req = CommandSubmitRequest(
        request_id="req-exp",
        control_session_id=session.session_id,
        target_id="motor01",
        action="move",
        params={"position": 5},
    )
    record = await app.submit_command(req)

    final = await run_to_completion(app, clock, record.command_id, timeout_ticks=1000)
    assert final.outcome == CommandOutcome.EXPIRED


async def test_unknown_action_is_rejected() -> None:
    app, _clock, _motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    req = CommandSubmitRequest(
        request_id="req-unk",
        control_session_id=session.session_id,
        target_id="motor01",
        action="teleport",
        params={},
    )
    with pytest.raises(FusionError) as excinfo:
        await app.submit_command(req)
    assert excinfo.value.code == ErrorCode.UNKNOWN_ACTION


async def test_unknown_target_is_rejected() -> None:
    app, _clock, _motor = make_app()
    session = await app.acquire_control_session("tester", RuntimeMode.MANUAL)

    req = CommandSubmitRequest(
        request_id="req-unk-t",
        control_session_id=session.session_id,
        target_id="motor99",
        action="move",
        params={"position": 1},
    )
    with pytest.raises(FusionError) as excinfo:
        await app.submit_command(req)
    assert excinfo.value.code == ErrorCode.UNKNOWN_TARGET
