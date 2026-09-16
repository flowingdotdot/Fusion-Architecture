"""DeviceRuntimeApp's blob transfer methods (doc section 14): wrap BlobStore
errors as the same FusionError contract every other rejection uses, and 404 for
an unknown sha256 on open, matching COMMAND_NOT_FOUND's precedent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from fusion.contracts.errors import ErrorCode, FusionError
from fusion.core.blob_store import BlobStore
from fusion.core.device_runtime import DeviceRuntimeApp
from fusion.simulation.fake_clock import FakeClock
from fusion.simulation.fake_motor import FakeMotorAdapter


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


def make_app(tmp_path: Path) -> DeviceRuntimeApp:
    clock = FakeClock()
    motor = FakeMotorAdapter("motor01", clock)
    return DeviceRuntimeApp(
        "motor-test",
        {"motor01": motor},
        clock,
        app_name="fusion-motor",
        blob_store=BlobStore(tmp_path),
    )


async def test_save_blob_then_open_blob_round_trips(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    ref = await app.save_blob(_chunks(b"payload bytes"))

    path = app.open_blob(ref.sha256)
    assert path.read_bytes() == b"payload bytes"


async def test_open_blob_raises_not_found_for_unknown_hash(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    with pytest.raises(FusionError) as exc_info:
        app.open_blob("0" * 64)
    assert exc_info.value.code == ErrorCode.BLOB_NOT_FOUND


async def test_save_blob_wraps_hash_mismatch_as_validation_error(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    with pytest.raises(FusionError) as exc_info:
        await app.save_blob(_chunks(b"payload bytes"), expected_sha256="0" * 64)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_save_blob_wraps_too_large_as_validation_error(tmp_path: Path) -> None:
    clock = FakeClock()
    motor = FakeMotorAdapter("motor01", clock)
    app = DeviceRuntimeApp(
        "motor-test",
        {"motor01": motor},
        clock,
        app_name="fusion-motor",
        blob_store=BlobStore(tmp_path, max_bytes=2),
    )

    with pytest.raises(FusionError) as exc_info:
        await app.save_blob(_chunks(b"too many bytes"))
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
