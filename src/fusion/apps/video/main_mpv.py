"""Real Video Runtime entrypoint: embeds libmpv into a bare Qt window (the actual
display output) and serves the same generic Command/Event/ControlSession HTTP+WS
API as Motor, from one process, plus one Video-specific extra route (a JPEG preview
snapshot) that doesn't fit the generic RuntimeApi contract.

Qt (the video surface) owns the main thread; asyncio (uvicorn + the Runtime) runs
on one background thread. This is the mirror image of ``ui/asyncio_bridge.py``
(which ran asyncio in the background *for a control UI* with Qt as the surface the
user clicks on) -- here Qt's only job is to own a window handle for mpv to render
into, so Qt stays on the thread it needs and asyncio gets the rest.

``uv run fusion-video-mpv [--screen N] [--windowed]``. Requires the "studio"
dependency group (``uv sync --group studio``) for PySide6 and python-mpv, PLUS an
actual libmpv DLL reachable on PATH -- doc section 17 explicitly keeps EXE/installer
packaging (which would bundle this) out of scope for now, so for a dev machine this
points at wherever libmpv already exists locally; a real deployment needs its own
bundled copy, not a dependency on this specific path.
"""

from __future__ import annotations

import os

# python-mpv's own module-level code looks for mpv-1.dll/mpv-2.dll/libmpv-2.dll on
# %PATH% the moment it's imported -- this must run before `import mpv` below, not
# just before first use, or the import itself raises OSError.
#
# Dev-machine-only: a known-good libmpv-2.dll from a prior player project on this
# machine. Not a Fusion dependency on that project's continued existence in
# production -- a real deployment must bundle its own libmpv.
_FALLBACK_LIBMPV_DIR = r"D:\Junyoung\Junyoung_Work\Work\dev\plower"
if os.path.isdir(_FALLBACK_LIBMPV_DIR) and _FALLBACK_LIBMPV_DIR not in os.environ["PATH"]:
    os.environ["PATH"] = _FALLBACK_LIBMPV_DIR + os.pathsep + os.environ["PATH"]

import argparse  # noqa: E402
import asyncio  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402

import mpv  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from fusion.apps.video.mpv_adapter import MpvPlayerAdapter  # noqa: E402
from fusion.apps.video.runtime import VideoRuntimeApp  # noqa: E402
from fusion.core.clock import RealClock  # noqa: E402
from fusion.telemetry.logging import configure_runtime_logging  # noqa: E402
from fusion.transport.server import build_app  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8104


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--screen", type=int, default=0, help="0-based display index to show the output on"
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="start windowed instead of fullscreen (dev convenience; fullscreen is the default)",
    )
    return parser.parse_args()


class VideoOutputWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Fusion Video Output")
        self.resize(960, 540)
        self.setStyleSheet("background-color: black;")
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        # No system-background paint and no update-on-resize erase -- this window's
        # only content is mpv's own GPU-rendered surface; letting Qt paint anything
        # of its own here (even briefly, before mpv attaches) is the "one frame
        # flicker" the window would otherwise show at startup.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def prepare_geometry(self, screen_index: int, *, fullscreen: bool) -> None:
        """Sets target screen/position/flags BEFORE the native window is forced
        into existence (via ``winId()``, so mpv has a handle to attach to) -- doing
        this the other way around (flags changed on an already-created native
        window) was observed to silently keep the window bordered and windowed
        instead of actually entering fullscreen."""
        screens = QGuiApplication.screens()
        screen = screens[screen_index] if 0 <= screen_index < len(screens) else screens[0]
        self.move(screen.geometry().topLeft())
        if fullscreen:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

    def reveal(self, *, fullscreen: bool) -> None:
        if fullscreen:
            self.showFullScreen()
        else:
            self.show()
            self.raise_()
            self.activateWindow()


def _run_server_thread(
    runtime: VideoRuntimeApp,
    adapter: MpvPlayerAdapter,
    stop_event: threading.Event,
) -> None:
    async def _main() -> None:
        await adapter.connect()
        app = build_app(runtime)

        @app.get("/api/v1/targets/{target_id}/preview")
        async def preview(target_id: str) -> FileResponse:
            """Not part of the generic RuntimeApi contract -- a small JPEG snapshot
            of the current frame so a control UI can show a live thumbnail without
            a second full video render pipeline."""
            if target_id != adapter.target_id:
                raise HTTPException(status_code=404, detail=f"unknown target '{target_id}'")
            path = os.path.join(tempfile.gettempdir(), f"fusion-video-preview-{target_id}.jpg")
            adapter.capture_preview(path)
            return FileResponse(path, media_type="image/jpeg")

        config = uvicorn.Config(app, host=DEFAULT_HOST, port=DEFAULT_PORT, log_level="info")
        server = uvicorn.Server(config)

        async def _watch_stop() -> None:
            while not stop_event.is_set():
                await asyncio.sleep(0.1)
            server.should_exit = True

        await asyncio.gather(server.serve(), _watch_stop())

    asyncio.run(_main())


def run() -> None:
    args = _parse_args()

    fullscreen = not args.windowed

    qt_app = QApplication(sys.argv)
    window = VideoOutputWindow()
    window.prepare_geometry(args.screen, fullscreen=fullscreen)
    window.winId()  # forces native window creation without making it visible yet

    player = mpv.MPV(
        wid=str(int(window.winId())),
        vo="gpu",
        hwdec="auto",
        keep_open="yes",
        force_window="immediate",
        log_handler=print,
        loglevel="error",
    )

    clock = RealClock()
    adapter = MpvPlayerAdapter("output1", player, clock)
    runtime = VideoRuntimeApp("video-mpv-01", {"output1": adapter}, clock)

    def _publish(event_type: str, payload: dict[str, object]) -> None:
        runtime.publish_event(event_type, target_id="output1", payload=payload)

    adapter.bind_event_publisher(_publish)
    configure_runtime_logging("video-mpv", runtime.runtime_id, runtime.runtime_boot_id)

    # mpv is fully attached and rendering (a black frame, same color as the widget's
    # own background) before the window is shown -- default is foreground+fullscreen
    # per the operator's expectation that a Video output is always front-and-center.
    window.reveal(fullscreen=fullscreen)

    stop_event = threading.Event()
    server_thread = threading.Thread(
        target=_run_server_thread, args=(runtime, adapter, stop_event), daemon=True
    )
    server_thread.start()

    exit_code = qt_app.exec()

    stop_event.set()
    server_thread.join(timeout=5.0)
    player.terminate()
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
