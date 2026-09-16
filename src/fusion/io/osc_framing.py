"""OSC 1.0 message encode/decode (doc section 13): the public Open Sound Control
spec (https://opensoundcontrol.org/spec-1_0), not vendor-specific -- same
"hand-roll the public wire format" approach as ``modbus_framing.py``.

Only OSC Messages are handled, not Bundles (a Bundle's first 8 bytes are the
literal string "#bundle\\0"); doc section 13 scopes this to what real inputs
actually need, and nothing here has asked for bundled/timed messages yet.
Argument types: i (int32), f (float32), s (string), b (blob), plus the common
no-payload boolean/nil extension tags T/F/N (widely supported in practice even
though strictly OSC 1.1).
"""

from __future__ import annotations

import struct
from typing import Any

BUNDLE_MARKER = b"#bundle\x00"


class OscError(Exception):
    """Malformed OSC message: truncated data, missing type tag string, or an
    unsupported type tag."""


def _osc_string_bytes(value: str) -> bytes:
    data = value.encode("ascii") + b"\x00"
    padding = (4 - (len(data) % 4)) % 4
    return data + b"\x00" * padding


def _read_osc_string(data: bytes, offset: int) -> tuple[str, int]:
    terminator = data.find(b"\x00", offset)
    if terminator == -1:
        raise OscError("unterminated OSC string")
    value = data[offset:terminator].decode("ascii", errors="replace")
    next_offset = terminator + 1
    padding = (4 - (next_offset % 4)) % 4
    return value, next_offset + padding


def build_osc_message(address: str, args: list[Any]) -> bytes:
    type_tags = ","
    arg_bytes = b""
    for arg in args:
        if isinstance(arg, bool):
            type_tags += "T" if arg else "F"
        elif isinstance(arg, int):
            type_tags += "i"
            arg_bytes += arg.to_bytes(4, "big", signed=True)
        elif isinstance(arg, float):
            type_tags += "f"
            arg_bytes += struct.pack(">f", arg)
        elif isinstance(arg, str):
            type_tags += "s"
            arg_bytes += _osc_string_bytes(arg)
        elif isinstance(arg, bytes | bytearray):
            type_tags += "b"
            arg_bytes += len(arg).to_bytes(4, "big") + bytes(arg)
            padding = (4 - (len(arg) % 4)) % 4
            arg_bytes += b"\x00" * padding
        else:
            raise TypeError(f"unsupported OSC argument type {type(arg)!r}")
    return _osc_string_bytes(address) + _osc_string_bytes(type_tags) + arg_bytes


def parse_osc_message(data: bytes) -> tuple[str, list[Any]]:
    if data.startswith(BUNDLE_MARKER):
        raise OscError("OSC bundles are not supported")
    if not data:
        raise OscError("empty OSC packet")

    address, offset = _read_osc_string(data, 0)
    if not address.startswith("/"):
        raise OscError(f"OSC address must start with '/', got {address!r}")
    if offset >= len(data):
        return address, []

    type_tags, offset = _read_osc_string(data, offset)
    if not type_tags.startswith(","):
        raise OscError("missing OSC type tag string")

    args: list[Any] = []
    for tag in type_tags[1:]:
        if tag == "i":
            if offset + 4 > len(data):
                raise OscError("truncated int32 argument")
            args.append(int.from_bytes(data[offset : offset + 4], "big", signed=True))
            offset += 4
        elif tag == "f":
            if offset + 4 > len(data):
                raise OscError("truncated float32 argument")
            args.append(struct.unpack(">f", data[offset : offset + 4])[0])
            offset += 4
        elif tag == "s":
            value, offset = _read_osc_string(data, offset)
            args.append(value)
        elif tag == "b":
            if offset + 4 > len(data):
                raise OscError("truncated blob size")
            size = int.from_bytes(data[offset : offset + 4], "big")
            offset += 4
            if offset + size > len(data):
                raise OscError("truncated blob data")
            args.append(data[offset : offset + size])
            offset += size
            offset += (4 - (offset % 4)) % 4
        elif tag in ("T", "F", "N", "I"):
            args.append({"T": True, "F": False, "N": None, "I": True}[tag])
        else:
            raise OscError(f"unsupported OSC type tag '{tag}'")
    return address, args
