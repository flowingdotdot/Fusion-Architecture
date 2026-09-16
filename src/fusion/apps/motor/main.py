"""Motor Runtime entrypoint. ``uv run fusion-motor`` (see pyproject [project.scripts]).

UI is a separate concern (doc section 4) -- there is no UI here, and closing a UI
window later must not be what stops this process.

Optional ``--mqtt host:port`` attaches the doc section 14 MQTT remote-management
Adapter (config Stage/Apply/status only, never motor moves) alongside the plain
HTTP API -- e.g. against HiveMQ's free public test broker for a quick real check:
``uv run fusion-motor --mqtt broker.hivemq.com:1883 --mqtt-prefix fusion-<your-unique-id>``
(a public broker has no auth/ACL at all, so the prefix is the only thing keeping
this Runtime's topics from colliding with anyone else's -- pick something unique,
never send anything sensitive through it).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn
from fastapi import FastAPI

from fusion.apps.motor.runtime import MotorRuntimeApp
from fusion.core.clock import RealClock
from fusion.simulation.fake_motor import FakeMotorAdapter
from fusion.telemetry.logging import configure_runtime_logging
from fusion.transport.mqtt_adapter import MqttSettingAdapter
from fusion.transport.server import build_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8101


def build() -> MotorRuntimeApp:
    clock = RealClock()
    adapters = {"motor01": FakeMotorAdapter("motor01", clock)}
    return MotorRuntimeApp("motor-01", adapters, clock)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mqtt", help="broker host:port, e.g. broker.hivemq.com:1883")
    parser.add_argument(
        "--mqtt-prefix",
        default="fusion",
        help="topic prefix (pick something unique on a public broker)",
    )
    parser.add_argument("--mqtt-username")
    parser.add_argument("--mqtt-password")
    return parser.parse_args()


def _split_host_port(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host, int(port)


async def _run_with_mqtt(
    runtime: MotorRuntimeApp,
    app: FastAPI,
    mqtt_host: str,
    mqtt_port: int,
    args: argparse.Namespace,
) -> None:
    clock = RealClock()
    mqtt_adapter = MqttSettingAdapter(
        mqtt_host,
        mqtt_port,
        runtime.runtime_id,
        runtime,
        clock,
        topic_prefix=args.mqtt_prefix,
        username=args.mqtt_username,
        password=args.mqtt_password,
    )
    await mqtt_adapter.start()
    print(f"MQTT enabled: {mqtt_host}:{mqtt_port} prefix='{args.mqtt_prefix}'")
    try:
        config = uvicorn.Config(app, host=DEFAULT_HOST, port=DEFAULT_PORT)
        await uvicorn.Server(config).serve()
    finally:
        await mqtt_adapter.stop()


def run() -> None:
    args = _parse_args()
    runtime = build()
    configure_runtime_logging("motor", runtime.runtime_id, runtime.runtime_boot_id)
    app = build_app(runtime)

    if args.mqtt:
        mqtt_host, mqtt_port = _split_host_port(args.mqtt)
        if sys.platform == "win32":
            # paho-mqtt (used by aiomqtt) needs add_reader/add_writer, which
            # Windows' default Proactor loop doesn't implement.
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(_run_with_mqtt(runtime, app, mqtt_host, mqtt_port, args))
    else:
        uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT)


if __name__ == "__main__":
    run()
