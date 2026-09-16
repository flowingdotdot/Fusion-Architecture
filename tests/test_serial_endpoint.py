"""SerialInputEndpoint (doc section 13): a fake serial connection (same
``SerialPort`` Protocol ``io/serial_session.py``'s tests use) stands in for a real
Arduino -- no real hardware needed to exercise the line-framing/parsing logic.
"""

from __future__ import annotations

import asyncio
import queue

from fusion.contracts.trigger import InputEvent
from fusion.core.clock import RealClock
from fusion.io.serial_endpoint import SerialInputEndpoint


class FakeLineSerialPort:
    """Each ``read()`` returns one previously-pushed line (with its newline) --
    granularity doesn't matter to the endpoint, which just accumulates bytes and
    splits on newlines regardless of how they arrive."""

    def __init__(self) -> None:
        self._queue: queue.Queue[bytes] = queue.Queue()
        self.closed = False

    def push_line(self, text: str) -> None:
        self._queue.put((text + "\n").encode("utf-8"))

    def read(self, size: int) -> bytes:
        try:
            return self._queue.get(timeout=0.1)
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> int:
        return len(data)

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


async def _wait_for(events: list[InputEvent], count: int, *, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while len(events) < count:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"expected {count} event(s), got {len(events)}")
        await asyncio.sleep(0.01)


async def test_json_line_is_parsed_into_event() -> None:
    events: list[InputEvent] = []

    async def on_event(event: InputEvent) -> None:
        events.append(event)

    fake_port = FakeLineSerialPort()
    endpoint = SerialInputEndpoint("COM_TEST", on_event, RealClock(), connection=fake_port)
    await endpoint.start()
    try:
        fake_port.push_line('{"type": "arduino_btn", "value": true}')
        await _wait_for(events, 1)
        assert events[0].source == "serial"
        assert events[0].type == "arduino_btn"
        assert events[0].value is True
    finally:
        await endpoint.stop()
    assert not fake_port.closed  # a caller-supplied connection is not the endpoint's to close


async def test_bare_text_line_becomes_a_true_valued_event() -> None:
    events: list[InputEvent] = []

    async def on_event(event: InputEvent) -> None:
        events.append(event)

    fake_port = FakeLineSerialPort()
    endpoint = SerialInputEndpoint("COM_TEST", on_event, RealClock(), connection=fake_port)
    await endpoint.start()
    try:
        fake_port.push_line("button_c")
        await _wait_for(events, 1)
        assert events[0].type == "button_c"
        assert events[0].value is True
    finally:
        await endpoint.stop()


async def test_multiple_lines_in_one_read_are_each_dispatched() -> None:
    events: list[InputEvent] = []

    async def on_event(event: InputEvent) -> None:
        events.append(event)

    fake_port = FakeLineSerialPort()
    endpoint = SerialInputEndpoint("COM_TEST", on_event, RealClock(), connection=fake_port)
    await endpoint.start()
    try:
        fake_port._queue.put(b"button_x\nbutton_y\n")  # noqa: SLF001 - simulate two lines arriving in one chunk
        await _wait_for(events, 2)
        assert [e.type for e in events] == ["button_x", "button_y"]
    finally:
        await endpoint.stop()
