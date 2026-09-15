"""Stage 2/3 flagship path (doc section 19): Scheduler (with a one-Cue Timeline) ->
Fake Motor over real HTTP + WebSocket -> command.completed Event -> Show state
transition. Uses RealClock (not FakeClock) since this drives an actual uvicorn
server/socket; the move distance is kept tiny so the test still runs in well under a
second.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
import uvicorn

from fusion.apps.motor.main import build as build_motor
from fusion.apps.scheduler.runtime import SchedulerRuntimeApp
from fusion.contracts.timeline import Cue, Timeline
from fusion.core.clock import RealClock
from fusion.core.show_controller import ShowState
from fusion.transport.client import RuntimeClient
from fusion.transport.server import build_app

PORT = 8199


@pytest.fixture
async def motor_server():
    motor_app = build_motor()
    fastapi_app = build_app(motor_app)
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield motor_app
    finally:
        # uvicorn's graceful shutdown (should_exit=True, then await the serve task)
        # was observed to hang here on Windows once a WebSocket connection had been
        # served -- cancelling the serve task directly avoids that shutdown path
        # entirely, which is fine for an ephemeral per-test server.
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_scheduler_runs_a_show_cue_over_real_http_and_websocket(motor_server) -> None:
    client = RuntimeClient(f"http://127.0.0.1:{PORT}")
    try:
        info = await client.get_info()
        assert info["runtime_id"] == "motor-01"

        scheduler = SchedulerRuntimeApp({"motor": client}, RealClock())
        scheduler.load_timeline(
            Timeline(
                cues=[
                    Cue(
                        cue_id="cue-1",
                        at_ms=0,
                        runtime="motor",
                        target_id="motor01",
                        action="move",
                        params={"position": 2},
                    )
                ]
            )
        )

        await scheduler.arm()
        assert scheduler.controller.state == ShowState.ARMED

        scheduler.start()
        for _ in range(200):
            if scheduler.controller.state != ShowState.RUNNING:
                break
            await asyncio.sleep(0.02)

        assert scheduler.controller.state == ShowState.IDLE
        assert scheduler.last_outcomes["cue-1"].outcome == "SUCCEEDED"
    finally:
        await client.aclose()


async def test_duplicate_cue_request_id_over_http_does_not_move_twice(motor_server) -> None:
    client = RuntimeClient(f"http://127.0.0.1:{PORT}")
    try:
        session = await client.acquire_session("tester", mode="MANUAL")
        first = await client.submit_command(
            request_id="req-dup-http",
            control_session_id=session["session_id"],
            target_id="motor01",
            action="move",
            params={"position": 3},
        )
        second = await client.submit_command(
            request_id="req-dup-http",
            control_session_id=session["session_id"],
            target_id="motor01",
            action="move",
            params={"position": 3},
        )
        assert first["command_id"] == second["command_id"]
    finally:
        await client.aclose()
