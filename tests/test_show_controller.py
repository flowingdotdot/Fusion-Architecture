"""ShowController state machine (doc section 11): one active Show, re-entry blocked
outright, HOLD has no Resume (only Abort), a Timeline failure faults the Show rather
than crashing the process.
"""

from __future__ import annotations

import asyncio

import pytest

from fusion.core.show_controller import ShowController, ShowState


async def _noop() -> None:
    return None


async def test_arm_then_start_reaches_running_then_idle_on_success() -> None:
    controller = ShowController()
    await controller.arm(_noop)
    assert controller.state == ShowState.ARMED

    controller.start(_noop, run_id="run-1")
    assert controller.state == ShowState.RUNNING

    for _ in range(100):
        if controller.state != ShowState.RUNNING:
            break
        await asyncio.sleep(0.01)
    assert controller.state == ShowState.IDLE


async def test_start_outside_armed_is_rejected_not_queued() -> None:
    controller = ShowController()
    with pytest.raises(RuntimeError):
        controller.start(_noop, run_id="run-1")
    assert controller.state == ShowState.IDLE


async def test_repeated_start_calls_do_not_reenter_a_running_show() -> None:
    controller = ShowController()
    await controller.arm(_noop)

    started = asyncio.Event()
    release = asyncio.Event()
    run_count = 0

    async def run() -> None:
        nonlocal run_count
        run_count += 1
        started.set()
        await release.wait()

    controller.start(run, run_id="run-1")
    await started.wait()

    # Every repeated "start" button press while RUNNING must be rejected outright.
    for _ in range(5):
        with pytest.raises(RuntimeError):
            controller.start(run, run_id="run-x")
    assert run_count == 1

    release.set()
    for _ in range(100):
        if controller.state != ShowState.RUNNING:
            break
        await asyncio.sleep(0.01)
    assert controller.state == ShowState.IDLE


async def test_arm_failure_returns_to_idle_and_reraises() -> None:
    controller = ShowController()

    async def failing_prepare() -> None:
        raise RuntimeError("motor unreachable")

    with pytest.raises(RuntimeError, match="motor unreachable"):
        await controller.arm(failing_prepare)
    assert controller.state == ShowState.IDLE
    assert controller.last_error == "motor unreachable"


async def test_run_failure_faults_the_show_without_crashing() -> None:
    controller = ShowController()
    await controller.arm(_noop)

    async def failing_run() -> None:
        raise RuntimeError("cue failed")

    controller.start(failing_run, run_id="run-1")
    for _ in range(100):
        if controller.state != ShowState.RUNNING:
            break
        await asyncio.sleep(0.01)
    assert controller.state == ShowState.FAULTED
    assert controller.last_error == "cue failed"


async def test_hold_then_abort_returns_to_idle_with_no_resume_path() -> None:
    controller = ShowController()
    await controller.arm(_noop)

    release = asyncio.Event()

    async def run() -> None:
        await release.wait()

    controller.start(run, run_id="run-1")
    controller.hold()
    assert controller.state == ShowState.HOLDING
    assert controller.hold_requested is True

    stopped = False

    async def stop_all() -> None:
        nonlocal stopped
        stopped = True
        release.set()

    await controller.abort(stop_all)
    assert stopped is True
    assert controller.state == ShowState.IDLE
    # No resume method exists on ShowController at all -- Abort-then-rearm only.
    assert not hasattr(controller, "resume")


async def test_hold_outside_running_is_rejected() -> None:
    controller = ShowController()
    with pytest.raises(RuntimeError):
        controller.hold()
