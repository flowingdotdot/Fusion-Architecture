"""OSC input Endpoint (doc section 13): "주소·인수와 외부 Action/Event 매핑" -- the
OSC address pattern (minus its leading '/') becomes the InputEvent's ``type``; the
first argument (if any) becomes ``value`` (default ``True`` for a bare address with
no arguments, e.g. a simple button-press message).
"""

from __future__ import annotations

import asyncio

from fusion.contracts.trigger import InputEvent
from fusion.core.clock import Clock
from fusion.io import osc_framing
from fusion.io.udp_endpoint import OnEvent


class OscInputEndpoint(asyncio.DatagramProtocol):
    def __init__(
        self, host: str, port: int, on_event: OnEvent, clock: Clock, *, source: str = "osc"
    ) -> None:
        self._host = host
        self._port = port
        self._on_event = on_event
        self._clock = clock
        self._source = source
        self._transport: asyncio.DatagramTransport | None = None

    @property
    def bound_port(self) -> int | None:
        if self._transport is None:
            return None
        sockname = self._transport.get_extra_info("sockname")
        return int(sockname[1]) if sockname else None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: self, local_addr=(self._host, self._port)
        )
        self._transport = transport

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            address, args = osc_framing.parse_osc_message(data)
        except osc_framing.OscError:
            return  # malformed/unsupported (e.g. a bundle) -- dropped, not crashed
        event_type = address.lstrip("/")
        value = args[0] if args else True
        if not isinstance(value, bool | float | int | str):
            return  # a blob argument has no sensible Trigger comparison value
        event = InputEvent(
            source=self._source, type=event_type, value=value, occurred_at=self._clock.now()
        )
        asyncio.create_task(self._on_event(event))
