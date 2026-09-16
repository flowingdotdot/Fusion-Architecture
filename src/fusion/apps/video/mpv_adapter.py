"""Real mpv-backed Video Player Target adapter (doc section 12, stage 5).

Embeds libmpv into a native Qt widget the same way a prior player project at
``D:\\...\\plower\\player.py`` proved out on this exact machine (``vo=gpu``,
``hwdec=auto``, ``keep_open=yes`` to avoid a black frame after playback ends) --
that technique is reused deliberately, not re-derived. Unlike that project, mpv's
lifetime here is the Video Runtime *process's* lifetime, never any control-UI
window's (doc section 4: "Player는 UI 수명에 종속시키지 않는다") -- there is no
control UI in this process, only the bare output surface.

Threading: the ``mpv.MPV`` instance and its backing widget must be constructed on
the Qt (main) thread -- see ``apps/video/main_mpv.py``. libmpv's client API
(commands, property get/set) is documented thread-safe, so this adapter's own
``execute``/``is_action_complete`` calls, made from the asyncio thread hosting the
HTTP/WS server, are safe. The one genuine cross-thread hazard is mpv's own
property-observer callback, which fires on a thread libmpv manages internally --
``connect()`` captures the running asyncio loop so that callback can hand off to it
via ``call_soon_threadsafe`` instead of touching asyncio state from the wrong thread.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fusion.contracts.plugin import ActionSpec, TargetManifest
from fusion.contracts.state import ObservedValue, TargetState
from fusion.core.clock import Clock
from fusion.plugin_sdk.base import TargetAdapter
from fusion.simulation.fake_video_player import PublishEvent

if TYPE_CHECKING:
    import mpv


class MpvPlayerAdapter(TargetAdapter):
    def __init__(self, target_id: str, player: mpv.MPV, clock: Clock) -> None:
        self.target_id = target_id
        self._player = player
        self._clock = clock

        self._media_path: str | None = None
        self._media_generation = 0
        self._connected = True
        self._was_playing = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._publish_event: PublishEvent | None = None

    def bind_event_publisher(self, publish: PublishEvent) -> None:
        self._publish_event = publish

    async def connect(self) -> None:
        """Captures the running loop and wires mpv's own idle-state notification
        to our "playback.ended" Event -- doc section 12's "play 명령의 성공과
        playback.ended는 구분한다" mapped onto mpv's actual idle/playing signal
        rather than an independently invented timer."""
        self._loop = asyncio.get_running_loop()
        self._player.observe_property("idle-active", self._on_idle_active)

    def manifest(self) -> TargetManifest:
        return TargetManifest(
            target_id=self.target_id,
            plugin_id="fusion.apps.video.mpv_adapter",
            plugin_version="0.1.0",
            actions=[
                ActionSpec(
                    name="prepare",
                    params_schema={"media_path": "string"},
                    max_completion="observed",
                    priority="normal",
                ),
                ActionSpec(
                    name="play", params_schema={}, max_completion="acknowledged", priority="normal"
                ),
                ActionSpec(
                    name="pause", params_schema={}, max_completion="acknowledged", priority="normal"
                ),
                ActionSpec(
                    name="stop", params_schema={}, max_completion="acknowledged", priority="control"
                ),
                ActionSpec(
                    name="seek",
                    params_schema={"position_s": "number"},
                    max_completion="acknowledged",
                    priority="normal",
                ),
                ActionSpec(
                    name="loop",
                    params_schema={"enabled": "boolean"},
                    max_completion="acknowledged",
                    priority="normal",
                ),
                ActionSpec(
                    name="volume",
                    params_schema={"level": "number (0-100)"},
                    max_completion="acknowledged",
                    priority="normal",
                ),
            ],
            capabilities=[],
        )

    async def execute(self, action: str, params: dict[str, Any]) -> None:
        if action == "prepare":
            self._media_generation += 1
            self._media_path = str(params["media_path"])
            self._player.command("loadfile", self._media_path, "replace")
            self._player.pause = True
        elif action == "play":
            if self._player.duration is None:
                raise ValueError("no media prepared")
            self._was_playing = True
            self._player.pause = False
        elif action == "pause":
            self._player.pause = True
        elif action == "stop":
            self._was_playing = False
            self._player.command("stop")
        elif action == "seek":
            self._player.seek(float(params["position_s"]), reference="absolute")
        elif action == "loop":
            self._player["loop-file"] = "inf" if bool(params["enabled"]) else "no"
        elif action == "volume":
            level = float(params["level"])
            if not (0.0 <= level <= 100.0):
                raise ValueError(f"volume {level} out of range 0-100")
            self._player.volume = level
        else:
            raise ValueError(f"unknown action '{action}'")

    def is_action_complete(self, action: str, params: dict[str, Any]) -> bool:
        if action == "prepare":
            return self._player.duration is not None
        return True

    def capture_preview(self, path: str) -> None:
        """Not part of the ``TargetAdapter`` interface -- a Video-specific extra a
        control UI can use for a live thumbnail (see ``apps/video/main_mpv.py``'s
        extra ``/preview`` route). Synchronous (mpv command dispatch + file I/O);
        fine for an occasional poll, not meant for a tight loop."""
        self._player.screenshot_to_file(path, includes="video")

    def snapshot(self) -> TargetState:
        now = self._clock.now()
        return TargetState(
            target_id=self.target_id,
            connection="CONNECTED" if self._connected else "DISCONNECTED",
            fields={
                "media_path": ObservedValue(value=self._media_path, observed_at=now),
                "prepared": ObservedValue(value=self._player.duration is not None, observed_at=now),
                "playing": ObservedValue(value=not bool(self._player.pause), observed_at=now),
                "position_s": ObservedValue(value=self._player.time_pos or 0.0, observed_at=now),
                "duration_s": ObservedValue(value=self._player.duration, observed_at=now),
                "volume": ObservedValue(value=self._player.volume, observed_at=now),
                "loop": ObservedValue(
                    value=self._player["loop-file"] not in (None, False, "no"), observed_at=now
                ),
                "media_generation": ObservedValue(value=self._media_generation, observed_at=now),
            },
        )

    def _on_idle_active(self, _name: str, value: bool) -> None:
        """Runs on mpv's own callback thread, NOT the asyncio thread."""
        if value and self._was_playing:
            self._was_playing = False
            generation = self._media_generation
            if self._loop is not None and self._publish_event is not None:
                self._loop.call_soon_threadsafe(
                    self._publish_event, "playback.ended", {"media_generation": generation}
                )
