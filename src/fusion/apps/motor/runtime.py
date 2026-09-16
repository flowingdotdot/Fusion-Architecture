"""Headless Motor Runtime (doc section 3-9, stage 1-2): owns Target Registry,
Command Ledger, per-Target execution, control sessions and the Event bus. No PySide6
import anywhere in this module -- a Studio UI (stage 3) would be a separate process
talking to this over the transport layer, never importing this module directly.

All the actual logic lives in ``core.device_runtime.DeviceRuntimeApp`` now (stage 5
extracted it once Video became a second real consumer of the exact same shape) --
this class is kept as a thin, stably-named subclass so existing imports/tests are
unaffected.
"""

from __future__ import annotations

from collections.abc import Mapping

from fusion.core.clock import Clock
from fusion.core.device_runtime import DeviceRuntimeApp
from fusion.plugin_sdk.base import TargetAdapter


class MotorRuntimeApp(DeviceRuntimeApp):
    def __init__(
        self, runtime_id: str, adapters: Mapping[str, TargetAdapter], clock: Clock
    ) -> None:
        super().__init__(runtime_id, adapters, clock, app_name="fusion-motor")
