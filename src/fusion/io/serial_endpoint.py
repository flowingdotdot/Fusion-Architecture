"""Serial input Endpoint (doc section 13): "framing·최대 길이·수신 timeout을 명시" --
framing is newline-delimited text lines (the common case for an Arduino writing via
``Serial.println``), a maximum line length guards against a runaway sender with no
newline ever filling memory, and the read timeout is the same bounded-blocking-read
pattern ``io/serial_session.py`` already uses so the worker thread can always notice
``stop()`` promptly rather than blocking forever.
"""

from __future__ import annotations

import asyncio
import queue
import threading

import serial

from fusion.contracts.trigger import InputEvent
from fusion.core.clock import Clock
from fusion.io.serial_session import SerialPort
from fusion.io.text_event_parsing import parse_text_event
from fusion.io.udp_endpoint import OnEvent

DEFAULT_BAUDRATE = 9600
DEFAULT_TIMEOUT_S = 0.2
MAX_LINE_BYTES = 4096


class SerialInputEndpoint:
    def __init__(
        self,
        port: str,
        on_event: OnEvent,
        clock: Clock,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        source: str = "serial",
        connection: SerialPort | None = None,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._on_event = on_event
        self._clock = clock
        self._source = source
        self._owns_connection = connection is None
        self._serial: SerialPort | None = connection
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_queue: queue.Queue[None] = queue.Queue()
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self._serial is None:
            self._serial = serial.Serial(
                port=self._port, baudrate=self._baudrate, timeout=DEFAULT_TIMEOUT_S
            )
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name=f"serial-input-{self._port}"
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop_queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._owns_connection and self._serial is not None:
            self._serial.close()

    def _worker(self) -> None:
        assert self._serial is not None
        buffer = b""
        while self._stop_queue.empty():
            chunk = self._serial.read(256)
            if not chunk:
                continue
            buffer += chunk
            if len(buffer) > MAX_LINE_BYTES:
                buffer = buffer[-MAX_LINE_BYTES:]  # drop the runaway partial line, keep reading
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace")
        event = parse_text_event(text, source=self._source, clock=self._clock)
        if event is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._dispatch, event)

    def _dispatch(self, event: InputEvent) -> None:
        asyncio.create_task(self._on_event(event))
