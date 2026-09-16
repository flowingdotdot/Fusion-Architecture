"""Raw UDP input Endpoint (doc section 13): datagram payload -> InputEvent, fed to
a TriggerEngine. Send success and receive confirmation are genuinely distinct for
UDP (doc: "송신 성공과 수신 확인 구분") -- this class only ever receives; there is no
delivery acknowledgement to the sender at this layer, by design (that would need an
application-level ack protocol this stage doesn't invent).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from fusion.contracts.trigger import InputEvent
from fusion.core.clock import Clock
from fusion.io.text_event_parsing import parse_text_event

OnEvent = Callable[[InputEvent], Coroutine[Any, Any, None]]


class UdpInputEndpoint(asyncio.DatagramProtocol):
    def __init__(
        self, host: str, port: int, on_event: OnEvent, clock: Clock, *, source: str = "udp"
    ) -> None:
        self._host = host
        self._port = port
        self._on_event = on_event
        self._clock = clock
        self._source = source
        self._transport: asyncio.DatagramTransport | None = None

    @property
    def bound_port(self) -> int | None:
        """The actual bound port -- useful when constructed with ``port=0`` (let
        the OS pick one), e.g. in tests."""
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
        event = parse_text_event(
            data.decode("utf-8", errors="replace"), source=self._source, clock=self._clock
        )
        if event is not None:
            asyncio.create_task(self._on_event(event))
