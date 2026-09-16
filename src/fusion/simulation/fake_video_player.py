"""Fake Video Player Target adapter (doc section 12, stage 5): no real mpv/media, so
Command/Event/completion machinery can be exercised deterministically before wiring
a real player. Actions match doc section 12's initial scope exactly: prepare/play/
pause/stop/seek/loop/volume.

"play" completes at "acknowledged" (playback started), never "observed" -- doc
section 12 explicitly warns against conflating a successful play command with the
video actually finishing ("play 명령의 성공과 playback.ended는 구분한다"). Ending is
reported as a spontaneous "playback.ended" Event instead, tagged with
media_generation so a stale "ended" from a since-superseded prepare can be told
apart from the current one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fusion.contracts.plugin import ActionSpec, TargetManifest
from fusion.contracts.state import ObservedValue, TargetState
from fusion.core.clock import Clock
from fusion.plugin_sdk.base import TargetAdapter

PublishEvent = Callable[[str, dict[str, Any]], None]


class FakeVideoPlayerAdapter(TargetAdapter):
    def __init__(self, target_id: str, clock: Clock, *, fake_duration_s: float = 2.0) -> None:
        self.target_id = target_id
        self._clock = clock
        self._fake_duration_s = fake_duration_s

        self._media_path: str | None = None
        self._media_generation = 0
        self._prepared = False
        self._playing = False
        self._position_s = 0.0
        self._volume = 100.0
        self._loop = False

        self._prepare_task: asyncio.Task[None] | None = None
        self._play_task: asyncio.Task[None] | None = None
        self._publish_event: PublishEvent | None = None

    def bind_event_publisher(self, publish: PublishEvent) -> None:
        """Not part of the ``TargetAdapter`` interface -- whoever assembles the
        Runtime wires this after construction (see ``apps/video/main.py``), so the
        base adapter contract stays free of Runtime-plumbing concerns."""
        self._publish_event = publish

    def manifest(self) -> TargetManifest:
        return TargetManifest(
            target_id=self.target_id,
            plugin_id="fusion.simulation.fake_video_player",
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
            self._prepared = False
            self._media_path = str(params["media_path"])
            if self._prepare_task is not None:
                self._prepare_task.cancel()
            self._prepare_task = asyncio.create_task(self._run_prepare(self._media_generation))
        elif action == "play":
            if not self._prepared:
                raise ValueError("no media prepared")
            self._playing = True
            if self._play_task is not None:
                self._play_task.cancel()
            self._play_task = asyncio.create_task(self._run_playback(self._media_generation))
        elif action == "pause":
            self._playing = False
            self._cancel_playback()
        elif action == "stop":
            self._playing = False
            self._position_s = 0.0
            self._cancel_playback()
        elif action == "seek":
            self._position_s = max(0.0, float(params["position_s"]))
        elif action == "loop":
            self._loop = bool(params["enabled"])
        elif action == "volume":
            level = float(params["level"])
            if not (0.0 <= level <= 100.0):
                raise ValueError(f"volume {level} out of range 0-100")
            self._volume = level
        else:
            raise ValueError(f"unknown action '{action}'")

    def _cancel_playback(self) -> None:
        if self._play_task is not None:
            self._play_task.cancel()
            self._play_task = None

    def is_action_complete(self, action: str, params: dict[str, Any]) -> bool:
        if action == "prepare":
            return self._prepared
        return True

    def snapshot(self) -> TargetState:
        now = self._clock.now()
        return TargetState(
            target_id=self.target_id,
            fields={
                "media_path": ObservedValue(value=self._media_path, observed_at=now),
                "prepared": ObservedValue(value=self._prepared, observed_at=now),
                "playing": ObservedValue(value=self._playing, observed_at=now),
                "position_s": ObservedValue(value=self._position_s, observed_at=now),
                "volume": ObservedValue(value=self._volume, observed_at=now),
                "loop": ObservedValue(value=self._loop, observed_at=now),
                "media_generation": ObservedValue(value=self._media_generation, observed_at=now),
            },
        )

    async def _run_prepare(self, generation: int) -> None:
        await self._clock.sleep(0.05)  # fake load time
        if generation == self._media_generation:
            self._prepared = True

    async def _run_playback(self, generation: int) -> None:
        remaining = self._fake_duration_s - self._position_s
        while remaining > 0:
            step = min(0.05, remaining)
            await self._clock.sleep(step)
            self._position_s += step
            remaining -= step
        self._playing = False
        if generation == self._media_generation and self._publish_event is not None:
            self._publish_event("playback.ended", {"media_generation": generation})
