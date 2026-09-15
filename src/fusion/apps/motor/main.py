"""Motor Runtime entrypoint. ``uv run fusion-motor`` (see pyproject [project.scripts]).

UI is a separate concern (doc section 4) -- there is no UI here, and closing a UI
window later must not be what stops this process.
"""

from __future__ import annotations

import uvicorn

from fusion.apps.motor.runtime import MotorRuntimeApp
from fusion.core.clock import RealClock
from fusion.simulation.fake_motor import FakeMotorAdapter
from fusion.telemetry.logging import configure_runtime_logging
from fusion.transport.server import build_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8101


def build() -> MotorRuntimeApp:
    clock = RealClock()
    adapters = {"motor01": FakeMotorAdapter("motor01", clock)}
    return MotorRuntimeApp("motor-01", adapters, clock)


def run() -> None:
    runtime = build()
    configure_runtime_logging("motor", runtime.runtime_id, runtime.runtime_boot_id)
    app = build_app(runtime)
    uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT)


if __name__ == "__main__":
    run()
