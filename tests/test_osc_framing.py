"""OSC 1.0 message encode/decode (doc section 13, public spec, not vendor-specific)."""

from __future__ import annotations

import pytest

from fusion.io import osc_framing as osc


def test_round_trip_int_float_string_args() -> None:
    frame = osc.build_osc_message("/fusion/button1", [1, 2.5, "hello"])
    address, args = osc.parse_osc_message(frame)
    assert address == "/fusion/button1"
    assert args[0] == 1
    assert args[1] == pytest.approx(2.5)
    assert args[2] == "hello"


def test_message_with_no_arguments() -> None:
    frame = osc.build_osc_message("/fusion/press", [])
    address, args = osc.parse_osc_message(frame)
    assert address == "/fusion/press"
    assert args == []


def test_boolean_args_round_trip() -> None:
    frame = osc.build_osc_message("/fusion/flag", [True, False])
    _address, args = osc.parse_osc_message(frame)
    assert args == [True, False]


def test_bundle_is_rejected_not_misparsed() -> None:
    with pytest.raises(osc.OscError, match="bundle"):
        osc.parse_osc_message(b"#bundle\x00" + b"\x00" * 8)


def test_missing_type_tag_string_is_rejected() -> None:
    address_only = osc._osc_string_bytes("/fusion/x")  # noqa: SLF001 - building a deliberately malformed frame
    with pytest.raises(osc.OscError, match="type tag"):
        osc.parse_osc_message(address_only + b"not-a-type-tag\x00")


def test_address_must_start_with_slash() -> None:
    frame = osc._osc_string_bytes("no-leading-slash") + osc._osc_string_bytes(",")  # noqa: SLF001
    with pytest.raises(osc.OscError, match="address"):
        osc.parse_osc_message(frame)


def test_truncated_int_argument_is_rejected() -> None:
    frame = osc._osc_string_bytes("/x") + osc._osc_string_bytes(",i") + b"\x00\x01"  # noqa: SLF001
    with pytest.raises(osc.OscError, match="truncated"):
        osc.parse_osc_message(frame)
