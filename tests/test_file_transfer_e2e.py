"""Real HTTP round trip for large file transfer (doc section 14: config Stage/Apply
uses small JSON, but "대용량 패키지·영상·대량 로그는 HTTPS로 전송한다") -- drives an
actual uvicorn server + RuntimeClient over a real socket, same pattern as
test_end_to_end_show.py, rather than only unit-testing BlobStore/DeviceRuntimeApp
in isolation.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest
import uvicorn

from fusion.core.blob_store import BlobStore
from fusion.core.clock import RealClock
from fusion.core.device_runtime import DeviceRuntimeApp
from fusion.simulation.fake_motor import FakeMotorAdapter
from fusion.transport.client import RuntimeClient
from fusion.transport.server import build_app

PORT = 8197


@pytest.fixture
async def motor_server(tmp_path: Path):
    clock = RealClock()
    motor_app = DeviceRuntimeApp(
        "motor-01",
        {"motor01": FakeMotorAdapter("motor01", clock)},
        clock,
        app_name="fusion-motor",
        blob_store=BlobStore(tmp_path),
    )
    fastapi_app = build_app(motor_app)
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield motor_app
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_upload_then_download_round_trips_real_bytes(
    motor_server, tmp_path: Path
) -> None:
    client = RuntimeClient(f"http://127.0.0.1:{PORT}")
    try:
        source = tmp_path / "source.bin"
        source.write_bytes(b"fusion large payload " * 10_000)

        result = await client.upload_file(source)
        assert "sha256" in result
        assert result["size"] == source.stat().st_size

        dest = tmp_path / "downloaded.bin"
        await client.download_file(result["sha256"], dest)

        assert dest.read_bytes() == source.read_bytes()
    finally:
        await client.aclose()


async def test_download_unknown_hash_returns_404(motor_server) -> None:
    client = RuntimeClient(f"http://127.0.0.1:{PORT}")
    try:
        with pytest.raises(Exception) as exc_info:  # httpx.HTTPStatusError
            await client.download_file("0" * 64, Path("unused"))
        assert "404" in str(exc_info.value)
    finally:
        await client.aclose()
