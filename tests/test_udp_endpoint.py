"""UdpInputEndpoint (doc section 13): real loopback UDP socket, no fake needed --
sending a datagram to 127.0.0.1 on an OS-assigned port is fully local and instant.
"""

from __future__ import annotations

import asyncio
import socket

from fusion.contracts.trigger import InputEvent
from fusion.core.clock import RealClock
from fusion.io.udp_endpoint import UdpInputEndpoint


async def _wait_for(events: list[InputEvent], count: int, *, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while len(events) < count:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"expected {count} event(s), got {len(events)}")
        await asyncio.sleep(0.01)


async def test_json_payload_is_parsed_into_type_and_value() -> None:
    events: list[InputEvent] = []

    async def on_event(event: InputEvent) -> None:
        events.append(event)

    endpoint = UdpInputEndpoint("127.0.0.1", 0, on_event, RealClock())
    await endpoint.start()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b'{"type": "button_a", "value": 42}', ("127.0.0.1", endpoint.bound_port))
        sock.close()

        await _wait_for(events, 1)
        assert events[0].source == "udp"
        assert events[0].type == "button_a"
        assert events[0].value == 42
    finally:
        await endpoint.stop()


async def test_bare_text_payload_becomes_a_true_valued_event() -> None:
    events: list[InputEvent] = []

    async def on_event(event: InputEvent) -> None:
        events.append(event)

    endpoint = UdpInputEndpoint("127.0.0.1", 0, on_event, RealClock())
    await endpoint.start()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b"button_b", ("127.0.0.1", endpoint.bound_port))
        sock.close()

        await _wait_for(events, 1)
        assert events[0].type == "button_b"
        assert events[0].value is True
    finally:
        await endpoint.stop()
