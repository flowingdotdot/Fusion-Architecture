"""Real-hardware Motor Runtime entrypoint for one MightyZap 17LF actuator over
Modbus RTU. Separate from ``fusion-motor`` (Fake Motor) so the default/test/demo
path never depends on real hardware being present.

Usage: ``uv run fusion-motor-mightyzap COM3`` (port is required -- there is no
sensible default to guess). Serial parity/stop bits are not documented by the
vendor manual; override via FUSION_MIGHTYZAP_PARITY / FUSION_MIGHTYZAP_STOPBITS
environment variables if the "N" / 1 default here turns out wrong against the real
device (confirm by whether reads come back with a valid CRC at all).
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from fusion.apps.motor.runtime import MotorRuntimeApp
from fusion.core.clock import RealClock
from fusion.io.serial_session import ModbusSerialSession
from fusion.plugin_sdk.loader import ensure_plugins_on_path
from fusion.telemetry.logging import configure_runtime_logging
from fusion.transport.server import build_app

ensure_plugins_on_path()
from mightyzap_modbus.adapter import MightyzapModbusAdapter  # noqa: E402,I001

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8102
STROKE_MM = 87.0  # "17LF-50F-87": 87mm-stroke variant per the manual's model/stroke table


async def _connect_all(adapters: dict[str, MightyzapModbusAdapter]) -> None:
    for adapter in adapters.values():
        await adapter.connect()


def build(serial_port: str) -> tuple[MotorRuntimeApp, dict[str, MightyzapModbusAdapter]]:
    clock = RealClock()
    session = ModbusSerialSession(serial_port)
    adapters = {
        "motor01": MightyzapModbusAdapter(
            "motor01", session, clock, slave_id=1, stroke_mm=STROKE_MM
        )
    }
    runtime = MotorRuntimeApp("motor-mightyzap-01", adapters, clock)
    return runtime, adapters


def run() -> None:
    if len(sys.argv) < 2:
        print("usage: fusion-motor-mightyzap <SERIAL_PORT>  (e.g. COM3)", file=sys.stderr)
        raise SystemExit(2)
    serial_port = sys.argv[1]

    runtime, adapters = build(serial_port)
    configure_runtime_logging("motor-mightyzap", runtime.runtime_id, runtime.runtime_boot_id)
    app = build_app(runtime)

    async def _main() -> None:
        # connect() (which starts each adapter's background poll task) and serve()
        # must share one event loop -- asyncio.run(connect) then a separate
        # uvicorn.run(serve) would close the loop the poll tasks were created on,
        # silently killing them before the server ever starts.
        await _connect_all(adapters)
        print(f"connected to MightyZap on {serial_port}, stroke={STROKE_MM}mm")
        server = uvicorn.Server(uvicorn.Config(app, host=DEFAULT_HOST, port=DEFAULT_PORT))
        await server.serve()

    asyncio.run(_main())


if __name__ == "__main__":
    run()
