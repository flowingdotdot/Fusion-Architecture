"""Headless Video Runtime (doc section 12, stage 5): one output's prepare/play/
pause/stop/seek/loop/volume, built on the same generic DeviceRuntimeApp Motor uses
-- Video's "Target" is the single output/player rather than a physical axis, but
the Command/Event/ControlSession shape doc section 6 describes is identical.
"""

from __future__ import annotations

from collections.abc import Mapping

from fusion.core.clock import Clock
from fusion.core.device_runtime import DeviceRuntimeApp
from fusion.plugin_sdk.base import TargetAdapter


class VideoRuntimeApp(DeviceRuntimeApp):
    def __init__(
        self, runtime_id: str, adapters: Mapping[str, TargetAdapter], clock: Clock
    ) -> None:
        super().__init__(runtime_id, adapters, clock, app_name="fusion-video")
