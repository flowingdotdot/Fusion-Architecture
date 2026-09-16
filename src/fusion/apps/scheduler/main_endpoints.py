"""Headless Scheduler driven by real external input Endpoints (doc section 13,
stage 6): enable any combination of Serial (e.g. an Arduino), UDP, or OSC -- each
parsed InputEvent feeds the same TriggerEngine a Studio UI's Fake button would (doc:
"외부 입력도... 등록된 Trigger/Command 경로로 들어온다"), never a device call directly.

Run a Motor Runtime first (``uv run fusion-motor``), then e.g.:
  uv run fusion-scheduler-endpoints --udp 0.0.0.0:9000
  uv run fusion-scheduler-endpoints --serial COM5:9600 --osc 0.0.0.0:9001

Any enabled source sending an InputEvent whose type matches ``--button-type``
(default "start") arms->fires the Show the same way regardless of which transport
carried it (a Trigger with ``source=None`` matches any source, doc section 13's own
"프로토콜 선택은 사용자에게 제공한다").
"""

from __future__ import annotations

import argparse
import asyncio

from fusion.apps.scheduler.runtime import SchedulerRuntimeApp
from fusion.contracts.event_source import EventSource
from fusion.contracts.timeline import Cue, Timeline
from fusion.contracts.trigger import Trigger, TriggerCondition
from fusion.core.clock import RealClock
from fusion.core.show_controller import ShowState
from fusion.io.osc_endpoint import OscInputEndpoint
from fusion.io.serial_endpoint import SerialInputEndpoint
from fusion.io.udp_endpoint import UdpInputEndpoint
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
    ]
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="COM port[:baudrate], e.g. COM5 or COM5:115200")
    parser.add_argument("--udp", help="host:port to listen on, e.g. 0.0.0.0:9000")
    parser.add_argument("--osc", help="host:port to listen on, e.g. 0.0.0.0:9001")
    parser.add_argument(
        "--button-type",
        default="start",
        help='InputEvent "type" that fires the Show, from any enabled source (default: "start")',
    )
    return parser.parse_args()


def _split_host_port(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return (host or "0.0.0.0", int(port))


def _split_port_baud(value: str) -> tuple[str, int]:
    port, _, baud = value.partition(":")
    return (port, int(baud)) if baud else (port, 9600)


async def _main() -> None:
    args = _parse_args()
    if not (args.serial or args.udp or args.osc):
        raise SystemExit("enable at least one of --serial / --udp / --osc")

    clock = RealClock()
    client = RuntimeClient(MOTOR_URL)
    scheduler = SchedulerRuntimeApp({"motor": client}, clock)
    scheduler.load_timeline(DEMO_TIMELINE)
    scheduler.register_trigger(
        Trigger(
            trigger_id="external-start",
            condition=TriggerCondition(type=args.button_type),  # source=None: any enabled Endpoint
            action="start",
            show_states=[ShowState.ARMED.value],
            debounce_ms=200,
            cooldown_ms=1000,
        )
    )
    configure_runtime_logging("scheduler-endpoints", "scheduler-endpoints", "boot-endpoints")

    endpoints: list[EventSource] = []
    if args.serial:
        serial_port, baud = _split_port_baud(args.serial)
        endpoints.append(
            SerialInputEndpoint(serial_port, scheduler.fire_input, clock, baudrate=baud)
        )
        print(f"serial input enabled on {serial_port} @ {baud}")
    if args.udp:
        udp_host, udp_port = _split_host_port(args.udp)
        endpoints.append(UdpInputEndpoint(udp_host, udp_port, scheduler.fire_input, clock))
        print(f"UDP input enabled on {udp_host}:{udp_port}")
    if args.osc:
        osc_host, osc_port = _split_host_port(args.osc)
        endpoints.append(OscInputEndpoint(osc_host, osc_port, scheduler.fire_input, clock))
        print(f"OSC input enabled on {osc_host}:{osc_port}")

    for endpoint in endpoints:
        await endpoint.start()

    try:
        info = await client.get_info()
        print(f"connected to {info['runtime_id']}; arming show...")
        await scheduler.arm()
        print(
            f"show state: {scheduler.controller.state.value} -- "
            f"waiting for a '{args.button_type}' input..."
        )

        armed_at = scheduler.controller.state
        while True:
            await asyncio.sleep(0.2)
            state = scheduler.controller.state
            if state != armed_at and state in (ShowState.IDLE, ShowState.FAULTED):
                print(
                    f"show state: {state.value} ({scheduler.controller.last_error or 'no error'})"
                )
                break
    finally:
        for endpoint in endpoints:
            await endpoint.stop()
        await client.aclose()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
