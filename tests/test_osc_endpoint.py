"""OscInputEndpoint (doc section 13): real loopback UDP socket carrying real
OSC-encoded bytes (built with the same framing module under test elsewhere).
"""

from __future__ import annotations

import asyncio
import socket

from fusion.contracts.trigger import InputEvent
from fusion.core.clock import RealClock
from fusion.io import osc_framing
from fusion.io.osc_endpoint import OscInputEndpoint


async def _wait_for(events: list[InputEvent], count: int, *, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while len(events) < count:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"expected {count} event(s), got {len(events)}")
        await asyncio.sleep(0.01)


async def test_address_and_first_argument_become_type_and_value() -> None:
    events: list[InputEvent] = []

    async def on_event(event: InputEvent) -> None:
        events.append(event)

    endpoint = OscInputEndpoint("127.0.0.1", 0, on_event, RealClock())
    await endpoint.start()
    try:
        frame = osc_framing.build_osc_message("/fusion/button1", [1])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(frame, ("127.0.0.1", endpoint.bound_port))
        sock.close()

        await _wait_for(events, 1)
        assert events[0].source == "osc"
        assert events[0].type == "fusion/button1"
        assert events[0].value == 1
    finally:
        await endpoint.stop()


async def test_address_with_no_arguments_defaults_value_true() -> None:
    events: list[InputEvent] = []

    async def on_event(event: InputEvent) -> None:
        events.append(event)

    endpoint = OscInputEndpoint("127.0.0.1", 0, on_event, RealClock())
    await endpoint.start()
    try:
        frame = osc_framing.build_osc_message("/fusion/press", [])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(frame, ("127.0.0.1", endpoint.bound_port))
        sock.close()

        await _wait_for(events, 1)
        assert events[0].value is True
    finally:
        await endpoint.stop()


async def test_malformed_packet_is_dropped_not_crashed() -> None:
    events: list[InputEvent] = []

    async def on_event(event: InputEvent) -> None:
        events.append(event)

    endpoint = OscInputEndpoint("127.0.0.1", 0, on_event, RealClock())
    await endpoint.start()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b"not an osc message", ("127.0.0.1", endpoint.bound_port))
        sock.sendto(osc_framing.build_osc_message("/ok", [1]), ("127.0.0.1", endpoint.bound_port))
        sock.close()

        await _wait_for(events, 1)  # only the well-formed second packet produced an event
        assert events[0].type == "ok"
    finally:
        await endpoint.stop()
