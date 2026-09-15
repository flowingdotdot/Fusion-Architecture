"""MightyzapModbusAdapter (doc section 9/18 stage 4): drives the adapter against a
fake serial port that speaks real Modbus RTU bytes (built with the same framing
module under test elsewhere) -- no real hardware, but genuine wire-protocol
round trips through ModbusSerialSession.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from mightyzap_modbus.adapter import (
    ACTUATOR_STOP,
    FORCE_ON_OFF,
    GOAL_POSITION,
    HARDWARE_ERROR,
    POLL_INTERVAL_S,
    MightyzapModbusAdapter,
)

from fusion.io import modbus_framing as mb
from fusion.io.serial_session import ModbusSerialSession
from fusion.simulation.fake_clock import FakeClock

STROKE_MM = 87.0


async def pump_poll(clock: FakeClock, condition: Callable[[], bool], *, ticks: int = 50) -> None:
    """Advances the adapter's background poll loop until ``condition()`` is true.

    Each poll transaction genuinely round-trips through a real OS thread (the
    session's worker thread), which FakeClock's virtual-time stepping alone cannot
    wait for -- an ``asyncio.sleep(0)`` after each ``advance()`` gives that thread's
    ``call_soon_threadsafe`` callback a real turn to land before the next tick.
    """
    for _ in range(ticks):
        if condition():
            return
        await clock.advance(POLL_INTERVAL_S)
        await asyncio.sleep(0)
    raise AssertionError("adapter state did not reflect the fake device in time")


class FakeMightyzapSerialPort:
    """Speaks the real Modbus RTU byte protocol (via ``modbus_framing``) over an
    in-memory register map, so tests exercise the actual wire encoding/decoding, not
    just the adapter's Python-level logic."""

    def __init__(self, slave_id: int = 1) -> None:
        self.slave_id = slave_id
        self.registers: dict[int, int] = dict.fromkeys(range(200, 220), 0)
        self._pending_response = b""

    def reset_input_buffer(self) -> None:
        self._pending_response = b""

    def write(self, data: bytes) -> int:
        slave, function_code = data[0], data[1]
        assert slave == self.slave_id
        if function_code == mb.READ_HOLDING_REGISTERS:
            address = (data[2] << 8) | data[3]
            count = (data[4] << 8) | data[5]
            values = [self.registers.get(address + i, 0) for i in range(count)]
            payload = bytes([slave, function_code, count * 2])
            for value in values:
                payload += value.to_bytes(2, "big")
        elif function_code == mb.WRITE_SINGLE_REGISTER:
            address = (data[2] << 8) | data[3]
            value = (data[4] << 8) | data[5]
            self._apply_write(address, value)
            payload = bytes(data[:6])
        else:
            raise AssertionError(f"unexpected function code {function_code:#x}")
        self._pending_response = payload + mb.crc16(payload).to_bytes(2, "little")
        return len(data)

    def read(self, size: int) -> bytes:
        chunk, self._pending_response = self._pending_response[:size], self._pending_response[size:]
        return chunk

    def close(self) -> None:
        pass

    def _apply_write(self, address: int, value: int) -> None:
        self.registers[address] = value
        if address == GOAL_POSITION:
            self.registers[215] = 1  # Moving -- doesn't arrive until the test calls arrive()
        elif address == ACTUATOR_STOP:
            self.registers[215] = 0

    def arrive(self) -> None:
        """Test helper: simulate the actuator having physically reached Goal Position."""
        self.registers[210] = self.registers[GOAL_POSITION]
        self.registers[215] = 0

    def set_alarm(self, bit_mask: int = 0x01) -> None:
        self.registers[HARDWARE_ERROR] = bit_mask


def make_adapter() -> tuple[
    MightyzapModbusAdapter, FakeMightyzapSerialPort, FakeClock, ModbusSerialSession
]:
    clock = FakeClock()
    port = FakeMightyzapSerialPort()
    session = ModbusSerialSession.from_connection(port)
    adapter = MightyzapModbusAdapter("motor01", session, clock, slave_id=1, stroke_mm=STROKE_MM)
    return adapter, port, clock, session


async def test_connect_enables_force_on_and_populates_initial_state() -> None:
    adapter, port, _clock, session = make_adapter()
    await adapter.connect()
    try:
        assert port.registers[FORCE_ON_OFF] == 1
        snapshot = adapter.snapshot()
        assert snapshot.connection == "CONNECTED"
        assert snapshot.fields["position"].value == 0.0
        assert snapshot.fields["moving"].value is False
    finally:
        await adapter.disconnect()
        session.close()


async def test_move_converts_mm_to_counts_and_reports_moving_until_arrival() -> None:
    adapter, port, clock, session = make_adapter()
    await adapter.connect()
    try:
        await adapter.execute("move", {"position": 43.5})  # half of 87mm stroke
        assert port.registers[GOAL_POSITION] == 5000  # 43.5/87 * 10000

        await pump_poll(clock, lambda: adapter.snapshot().fields["moving"].value is True)
        assert adapter.is_action_complete("move", {"position": 43.5}) is False

        port.arrive()
        await pump_poll(clock, lambda: adapter.is_action_complete("move", {"position": 43.5}))
        assert adapter.snapshot().fields["position"].value == pytest.approx(43.5)
    finally:
        await adapter.disconnect()
        session.close()


async def test_move_out_of_range_is_rejected_before_any_write() -> None:
    adapter, port, _clock, session = make_adapter()
    await adapter.connect()
    try:
        with pytest.raises(ValueError, match="out of range"):
            await adapter.execute("move", {"position": 200.0})
        assert port.registers[GOAL_POSITION] == 0
    finally:
        await adapter.disconnect()
        session.close()


async def test_stop_clears_moving() -> None:
    adapter, port, clock, session = make_adapter()
    await adapter.connect()
    try:
        await adapter.execute("move", {"position": 10.0})
        await adapter.execute("stop", {})
        assert port.registers[ACTUATOR_STOP] == 1

        await pump_poll(clock, lambda: adapter.is_action_complete("stop", {}))
    finally:
        await adapter.disconnect()
        session.close()


async def test_alarm_blocks_further_moves_without_silent_retry() -> None:
    adapter, port, clock, session = make_adapter()
    await adapter.connect()
    try:
        port.set_alarm()
        await pump_poll(clock, lambda: adapter.snapshot().fields["alarm"].value is True)

        with pytest.raises(ValueError, match="alarm"):
            await adapter.execute("move", {"position": 10.0})
    finally:
        await adapter.disconnect()
        session.close()
