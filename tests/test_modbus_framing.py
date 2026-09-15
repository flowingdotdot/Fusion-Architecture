"""Modbus RTU framing (doc section 18: "부분 수신·잘못된 프레임·늦은 응답 시험").
CRC16/frame shape is the public Modbus RTU spec, not manufacturer-specific.
"""

from __future__ import annotations

import pytest

from fusion.io import modbus_framing as mb


def test_crc16_matches_a_well_known_modbus_test_vector() -> None:
    # "01 03 00 00 00 0A" (slave 1, read holding registers, addr 0, count 10) is a
    # commonly published Modbus RTU CRC16 worked example; its CRC is 0xCDC5.
    assert mb.crc16(bytes.fromhex("01030000000a")) == 0xCDC5


def test_build_read_holding_registers_round_trips_through_parse() -> None:
    frame = mb.build_read_holding_registers(slave_id=1, address=210, count=2)
    assert frame[:6] == bytes([1, 0x03, 0x00, 210, 0x00, 0x02])

    # Simulate the device's response: echo slave+func, byte_count=4, two register values.
    payload = bytes([1, 0x03, 4, 0x12, 0x34, 0x00, 0x01])
    response = payload + mb.crc16(payload).to_bytes(2, "little")
    values = mb.parse_read_holding_registers_response(1, response)
    assert values == [0x1234, 0x0001]


def test_build_write_single_register_round_trips_through_parse() -> None:
    frame = mb.build_write_single_register(slave_id=1, address=206, value=5000)
    assert frame[:6] == bytes([1, 0x06, 0x00, 206, (5000 >> 8) & 0xFF, 5000 & 0xFF])

    payload = bytes([1, 0x06, 0x00, 206, (5000 >> 8) & 0xFF, 5000 & 0xFF])
    response = payload + mb.crc16(payload).to_bytes(2, "little")
    mb.parse_write_single_register_response(1, 206, 5000, response)  # must not raise


def test_bad_crc_is_rejected() -> None:
    payload = bytes([1, 0x03, 2, 0x00, 0x05])
    response = payload + bytes([0x00, 0x00])  # deliberately wrong CRC
    with pytest.raises(mb.FrameError, match="CRC"):
        mb.parse_read_holding_registers_response(1, response)


def test_short_response_is_rejected_not_indexed_into() -> None:
    with pytest.raises(mb.FrameError, match="too short"):
        mb.parse_read_holding_registers_response(1, bytes([1, 0x03]))


def test_wrong_slave_id_is_rejected() -> None:
    payload = bytes([2, 0x03, 2, 0x00, 0x05])
    response = payload + mb.crc16(payload).to_bytes(2, "little")
    with pytest.raises(mb.FrameError, match="slave id"):
        mb.parse_read_holding_registers_response(1, response)


def test_exception_response_is_reported_as_device_error() -> None:
    payload = bytes([1, 0x03 | 0x80, 0x02])  # illegal data address
    response = payload + mb.crc16(payload).to_bytes(2, "little")
    with pytest.raises(mb.DeviceError) as excinfo:
        mb.parse_read_holding_registers_response(1, response)
    assert excinfo.value.exception_code == 0x02


def test_write_response_echo_mismatch_is_rejected() -> None:
    payload = bytes([1, 0x06, 0x00, 206, 0x00, 0x01])  # echoes value=1, not what we sent
    response = payload + mb.crc16(payload).to_bytes(2, "little")
    with pytest.raises(mb.FrameError, match="echo"):
        mb.parse_write_single_register_response(1, 206, 5000, response)
