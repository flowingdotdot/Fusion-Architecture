"""Modbus RTU framing (doc section 9/22): CRC16 and frame build/parse for the two
function codes the MightyZap 17LF manual explicitly says it uses -- "Read Holding
Register" and "Write Single Register". These names map to the *standard* Modbus
function codes 0x03 and 0x06 defined by the public Modbus spec itself; that mapping,
the CRC16 algorithm, and the general RTU frame shape (slave id, function code, data,
CRC16) are the open protocol, not manufacturer-specific -- unlike register meanings,
units, or serial parity/stop-bit settings, which the manual does NOT state and must
not be guessed (see MightyzapModbusAdapter's module docstring).

Register addresses are used as given in the manual's "Address" column, confirmed
0-based by the manual's own example ("Register Number: 40001, Address: 0").
"""

from __future__ import annotations

READ_HOLDING_REGISTERS = 0x03
WRITE_SINGLE_REGISTER = 0x06
_EXCEPTION_BIT = 0x80


class ModbusError(Exception):
    """Base for anything wrong with a Modbus RTU exchange."""


class FrameError(ModbusError):
    """Malformed frame: too short, bad CRC, wrong slave id, or an unexpected
    function code. Covers doc section 18's "실제 통신 parser에는 부분 수신·잘못된
    프레임" requirement."""


class DeviceError(ModbusError):
    """The slave returned a well-formed Modbus exception response."""

    def __init__(self, function_code: int, exception_code: int) -> None:
        super().__init__(
            f"device returned exception code {exception_code:#x} for function {function_code:#x}"
        )
        self.function_code = function_code
        self.exception_code = exception_code


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _with_crc(frame: bytes) -> bytes:
    crc = crc16(frame)
    return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def build_read_holding_registers(slave_id: int, address: int, count: int) -> bytes:
    frame = bytes(
        [
            slave_id,
            READ_HOLDING_REGISTERS,
            (address >> 8) & 0xFF,
            address & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF,
        ]
    )
    return _with_crc(frame)


def build_write_single_register(slave_id: int, address: int, value: int) -> bytes:
    frame = bytes(
        [
            slave_id,
            WRITE_SINGLE_REGISTER,
            (address >> 8) & 0xFF,
            address & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ]
    )
    return _with_crc(frame)


def _check_frame(slave_id: int, response: bytes) -> None:
    if len(response) < 5:
        raise FrameError(f"response too short ({len(response)} bytes)")
    if response[0] != slave_id:
        raise FrameError(f"unexpected slave id {response[0]} (expected {slave_id})")
    received_crc = response[-2] | (response[-1] << 8)
    computed_crc = crc16(response[:-2])
    if received_crc != computed_crc:
        raise FrameError("CRC mismatch")


def parse_read_holding_registers_response(slave_id: int, response: bytes) -> list[int]:
    _check_frame(slave_id, response)
    function_code = response[1]
    if function_code == (READ_HOLDING_REGISTERS | _EXCEPTION_BIT):
        raise DeviceError(READ_HOLDING_REGISTERS, response[2])
    if function_code != READ_HOLDING_REGISTERS:
        raise FrameError(f"unexpected function code {function_code:#x}")
    byte_count = response[2]
    if len(response) != 3 + byte_count + 2:
        raise FrameError("response length does not match its own byte count field")
    return [(response[3 + i] << 8) | response[3 + i + 1] for i in range(0, byte_count, 2)]


def parse_write_single_register_response(
    slave_id: int, address: int, value: int, response: bytes
) -> None:
    _check_frame(slave_id, response)
    function_code = response[1]
    if function_code == (WRITE_SINGLE_REGISTER | _EXCEPTION_BIT):
        raise DeviceError(WRITE_SINGLE_REGISTER, response[2])
    if function_code != WRITE_SINGLE_REGISTER:
        raise FrameError(f"unexpected function code {function_code:#x}")
    if len(response) != 8:
        raise FrameError(f"write response has unexpected length {len(response)}")
    echoed_address = (response[2] << 8) | response[3]
    echoed_value = (response[4] << 8) | response[5]
    if echoed_address != address or echoed_value != value:
        raise FrameError("write response does not echo the request")
