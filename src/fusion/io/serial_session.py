"""Shared RS485/Modbus RTU serial session (doc section 9): one physical serial port
is owned by exactly one Session; every Target on that multi-drop bus shares it and
requests are naturally serialized (RS485 is half-duplex, only one transaction can be
in flight at a time regardless).

Blocking pyserial I/O runs on one dedicated worker thread (doc: "블로킹 serial은
제한된 전용 worker로 처리한다") so it never blocks the asyncio loop; async callers
submit a request and await a Future that the worker thread resolves via
``call_soon_threadsafe``.

Serial parity/stop-bit settings are NOT documented by the MightyZap manual (verified
by direct quote-only re-check, not inferred) -- they are constructor parameters here,
not a hardcoded assumption, and must be confirmed empirically against real hardware
before being treated as correct.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass
from typing import Protocol

import serial

from fusion.io import modbus_framing as mb

DEFAULT_BAUDRATE = 57600  # MightyZap 17LF manual's stated default
DEFAULT_TIMEOUT_S = 0.3


class SerialPort(Protocol):
    """The minimal slice of pyserial's ``Serial`` this module actually uses --
    lets tests substitute a fake device double without opening a real port."""

    def write(self, data: bytes) -> int | None: ...
    def read(self, size: int) -> bytes: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


@dataclass
class _Transaction:
    frame: bytes
    is_read: bool
    future: asyncio.Future[bytes]
    loop: asyncio.AbstractEventLoop


class ModbusSerialSession:
    def __init__(
        self,
        port: str,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        parity: str = serial.PARITY_NONE,
        stopbits: float = serial.STOPBITS_ONE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        connection = serial.Serial(
            port=port,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            bytesize=serial.EIGHTBITS,
            timeout=timeout_s,
        )
        self.port = port
        self._init_with_connection(connection)

    @classmethod
    def from_connection(
        cls, connection: SerialPort, *, port: str = "<test>"
    ) -> ModbusSerialSession:
        """Test/advanced-use constructor: skips opening a real OS serial port,
        driving ``connection`` directly instead."""
        session = cls.__new__(cls)
        session.port = port
        session._init_with_connection(connection)
        return session

    def _init_with_connection(self, connection: SerialPort) -> None:
        self._serial = connection
        self._queue: queue.Queue[_Transaction | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name=f"modbus-{self.port}"
        )
        self._thread.start()

    def _worker(self) -> None:
        while True:
            transaction = self._queue.get()
            if transaction is None:
                return
            try:
                response = self._transact(transaction.frame, transaction.is_read)
            except Exception as exc:  # noqa: BLE001 - forwarded to the awaiting coroutine, not swallowed
                transaction.loop.call_soon_threadsafe(
                    self._resolve_exception, transaction.future, exc
                )
                continue
            transaction.loop.call_soon_threadsafe(
                self._resolve_result, transaction.future, response
            )

    @staticmethod
    def _resolve_result(future: asyncio.Future[bytes], response: bytes) -> None:
        # The awaiting task may have been cancelled (e.g. Target shutdown) while this
        # transaction was already in flight on the worker thread -- the in-progress
        # I/O can't be aborted, so the result just arrives too late; setting it on an
        # already-done Future would raise InvalidStateError instead of being a no-op.
        if not future.done():
            future.set_result(response)

    @staticmethod
    def _resolve_exception(future: asyncio.Future[bytes], exc: Exception) -> None:
        if not future.done():
            future.set_exception(exc)

    def _transact(self, frame: bytes, is_read: bool) -> bytes:
        self._serial.reset_input_buffer()
        self._serial.write(frame)
        header = self._serial.read(3)
        if len(header) < 3:
            return header  # too short; the parser raises FrameError with a clear reason
        function_code = header[1]
        if function_code & 0x80:
            return header + self._serial.read(2)  # exception response is always 5 bytes total
        if is_read:
            byte_count = header[2]
            return header + self._serial.read(byte_count + 2)
        return header + self._serial.read(
            5
        )  # normal write-single-register echo is always 8 bytes total

    async def _submit(self, frame: bytes, *, is_read: bool) -> bytes:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        self._queue.put(_Transaction(frame=frame, is_read=is_read, future=future, loop=loop))
        return await future

    async def read_holding_registers(self, slave_id: int, address: int, count: int) -> list[int]:
        frame = mb.build_read_holding_registers(slave_id, address, count)
        response = await self._submit(frame, is_read=True)
        return mb.parse_read_holding_registers_response(slave_id, response)

    async def write_single_register(self, slave_id: int, address: int, value: int) -> None:
        frame = mb.build_write_single_register(slave_id, address, value)
        response = await self._submit(frame, is_read=False)
        mb.parse_write_single_register_response(slave_id, address, value, response)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=2.0)
        self._serial.close()
