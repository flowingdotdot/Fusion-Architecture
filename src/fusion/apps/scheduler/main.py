"""Manual demo entrypoint: run a Motor Runtime separately (``uv run fusion-motor``),
then run this to load a two-Cue Timeline, arm a Show, and start it -- watch the
Motor Runtime move to 100 then, once that succeeds, to 300.
``uv run fusion-scheduler`` (see pyproject [project.scripts]).
"""

from __future__ import annotations

import asyncio

from fusion.apps.scheduler.runtime import SchedulerRuntimeApp
from fusion.contracts.timeline import Cue, Timeline
from fusion.core.clock import RealClock
from fusion.core.show_controller import ShowState
from fusion.telemetry.logging import configure_runtime_logging
from fusion.transport.client import RuntimeClient

MOTOR_URL = "http://127.0.0.1:8101"

DEMO_TIMELINE = Timeline(
    cues=[
        Cue(
            cue_id="cue-1",
            at_ms=0,
            runtime="motor",
            target_id="motor01",
            action="move",
            params={"position": 100},
        ),
        Cue(
            cue_id="cue-2",
            at_ms=200,
            runtime="motor",
            target_id="motor01",
            action="move",
            params={"position": 300},
            depends_on=["cue-1"],
        ),
    ]
)


async def _main() -> None:
    configure_runtime_logging("scheduler", "scheduler-demo", "boot-demo")
    clock = RealClock()
    client = RuntimeClient(MOTOR_URL)
    try:
        info = await client.get_info()
        print(f"connected to {info['runtime_id']} (boot {info['runtime_boot_id']})")

        scheduler = SchedulerRuntimeApp({"motor": client}, clock)
        scheduler.load_timeline(DEMO_TIMELINE)

        await scheduler.arm()
        print(f"show state: {scheduler.controller.state.value}")

        scheduler.start()
        print(f"show state: {scheduler.controller.state.value}")

        while scheduler.controller.state == ShowState.RUNNING:
            await asyncio.sleep(0.05)

        print(f"show state: {scheduler.controller.state.value}")
        print(f"cue outcomes: {[(cid, o.outcome) for cid, o in scheduler.last_outcomes.items()]}")
    finally:
        await client.aclose()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
