"""Video Runtime entrypoint (Fake player -- doc section 19 stage 5 default path, no
real hardware/mpv dependency). ``uv run fusion-video`` (see pyproject
[project.scripts]), binds :8103 so it can run alongside Motor (:8101).
"""

from __future__ import annotations

import uvicorn

from fusion.apps.video.runtime import VideoRuntimeApp
from fusion.core.clock import RealClock
from fusion.simulation.fake_video_player import FakeVideoPlayerAdapter
from fusion.telemetry.logging import configure_runtime_logging
from fusion.transport.server import build_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8103


def build() -> VideoRuntimeApp:
    clock = RealClock()
    adapter = FakeVideoPlayerAdapter("output1", clock)
    runtime = VideoRuntimeApp("video-01", {"output1": adapter}, clock)

    def _publish(event_type: str, payload: dict[str, object]) -> None:
        runtime.publish_event(event_type, target_id="output1", payload=payload)

    adapter.bind_event_publisher(_publish)
    return runtime


def run() -> None:
    runtime = build()
    configure_runtime_logging("video", runtime.runtime_id, runtime.runtime_boot_id)
    app = build_app(runtime)
    uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT)


if __name__ == "__main__":
    run()
