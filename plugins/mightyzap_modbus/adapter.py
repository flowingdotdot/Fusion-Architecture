"""MightyZap 17LF Modbus RTU Target adapter (doc section 9, stage 4).

Manufacturer name/function preserved: MightyZap's own register names (Goal Position,
Present Position, Moving, Force On/Off, Actuator Stop, Hardware Error) are used
as-is; only the call/validate/result/state/stop *shape* is unified with the rest of
Fusion via ``TargetAdapter``.

Source: MightyZap 17LF e-manual
(https://mightyzap-emanual.netlify.app/actuator/mini17lf/manual/17lf_manual.html),
the "Volatile Memory (RAM)" control table, re-verified by a second verbatim-only
fetch after the first summarization pass mis-shifted two rows (an earlier version
of this file had Hardware Error/Goal Position at 205/206; confirmed wrong against
real hardware -- see "Verified against real hardware" below) and cross-checked for
internal consistency (every row's decimal address must equal its own hex address;
that check alone catches transcription slips like the one that happened here):

    200 (0xC8) Force On/Off          RW  default 1
    201 (0xC9) Actuator Pause        RW  default 0
    202 (0xCA) Actuator Stop         RW  write 1 -> immediate stop, Goal Position
                                          snaps to Present Position
    204 (0xCC) Hardware Error        R   default 0
    205 (0xCD) Goal Position         RW  0-10000 (fraction of full stroke)
    208 (0xD0) Goal Speed            RW  speed limit
    209 (0xD1) Goal Current          RW  current limit
    210 (0xD2) Present Position      R   0-10000
    211 (0xD3) Present Current       R
    213 (0xD5) Present Motor PWM     R
    214 (0xD6) Present Voltage       R   x0.1V
    215 (0xD7) Moving                R   0=stopped, 1=in motion
    217 (0xD9) Present Overload      R   0-100 (%)
    220 (0xDC) Action Enable         RW
    230 (0xE6) Reset                 W
    231 (0xE7) Restart               W
  204-215 inclusive is one contiguous block (with three undocumented gaps at 206,
  207, 212 that this device answers with 0), read in a single Read Holding
  Registers transaction per poll tick.

Verified against real hardware (2026-09-15, MightyZap 17LF-50F-87 over a USB-RS485
adapter, 57600 8N1, slave id 1): writing Goal Position (205) alone was sufficient to
start real motion (current/PWM rose, Moving went 1, position tracked to the
commanded value and Moving returned to 0 on arrival) -- "Action Enable" (220) was
NOT required for this unit/firmware, so it is not used here. The prior 205/206
addresses produced writes that echoed successfully (valid Modbus frames) but never
moved the actuator, which is exactly the failure mode a wrong-but-well-formed
address produces -- a reminder that a successful protocol exchange does not confirm
a correct register map.

- Position is 0-10000 counts = fraction of the physical stroke length; this
  adapter's Command-level "position" param is in mm, using a per-Target
  ``stroke_mm`` config (this unit's manual lists 27/37/50/87mm stroke variants --
  the "17LF-50F-87" model name's "87" is the 87mm-stroke variant, per the manual's
  own model/stroke table) to convert mm <-> counts.
- Completion is detected via the "Moving" bit going 0 (the manual's own "Method 1"),
  not an independently invented position-tolerance check.
- Serial parity/stop-bit settings are NOT stated anywhere in the manual (confirmed
  by a verbatim-only re-check, not inferred); 8N1 was confirmed empirically against
  real hardware (see above) -- ``ModbusSerialSession`` still takes them as explicit
  parameters rather than hardcoding 8N1 as if the manual specified it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fusion.contracts.plugin import ActionSpec, TargetManifest
from fusion.contracts.state import ObservedValue, Quality, TargetState
from fusion.core.clock import Clock
from fusion.io.serial_session import ModbusSerialSession
from fusion.plugin_sdk.base import TargetAdapter

FORCE_ON_OFF = 200
ACTUATOR_PAUSE = 201
ACTUATOR_STOP = 202
HARDWARE_ERROR = 204
GOAL_POSITION = 205
RESTART = 231

POLL_BLOCK_START = HARDWARE_ERROR  # 204
POLL_BLOCK_COUNT = 12  # covers 204..215 inclusive
OFFSET_HW_ERROR = HARDWARE_ERROR - POLL_BLOCK_START
OFFSET_PRESENT_POSITION = 210 - POLL_BLOCK_START
OFFSET_MOVING = 215 - POLL_BLOCK_START

FULL_STROKE_COUNTS = 10000
POLL_INTERVAL_S = 0.1
STALE_AFTER_S = POLL_INTERVAL_S * 5


class MightyzapModbusAdapter(TargetAdapter):
    def __init__(
        self,
        target_id: str,
        session: ModbusSerialSession,
        clock: Clock,
        *,
        slave_id: int = 1,
        stroke_mm: float,
    ) -> None:
        self.target_id = target_id
        self._session = session
        self._clock = clock
        self._slave_id = slave_id
        self._stroke_mm = stroke_mm

        self._position_mm = 0.0
        self._moving = False
        self._alarm = False
        self._connected = False
        self._observed_at = clock.now()
        self._poll_task: asyncio.Task[None] | None = None

    def manifest(self) -> TargetManifest:
        return TargetManifest(
            target_id=self.target_id,
            plugin_id="fusion.plugins.mightyzap_modbus",
            plugin_version="0.1.0",
            actions=[
                ActionSpec(
                    name="move",
                    params_schema={"position": f"number (mm, 0-{self._stroke_mm})"},
                    max_completion="observed",
                    priority="normal",
                ),
                ActionSpec(
                    name="stop", params_schema={}, max_completion="acknowledged", priority="control"
                ),
                ActionSpec(
                    name="restart",
                    params_schema={},
                    max_completion="acknowledged",
                    priority="control",
                ),
            ],
            capabilities=["stop_motion"],
        )

    async def connect(self) -> None:
        """Enables the actuator (Force On/Off=1, manual's own "Enable Motor"
        sequence) and does one synchronous register read so a dead/misconfigured
        link fails loudly at startup rather than silently. Only then starts the
        background poll loop -- doc section 9: don't touch hardware at import, only
        once a Target is genuinely being brought into service."""
        await self._session.write_single_register(self._slave_id, FORCE_ON_OFF, 1)
        await self._poll_once()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def disconnect(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def execute(self, action: str, params: dict[str, Any]) -> None:
        if action == "move":
            if self._alarm:
                raise ValueError(
                    "hardware alarm active (Hardware Error register nonzero) -- "
                    "write Restart (0xE7) or power-cycle before moving"
                )
            position_mm = float(params["position"])
            if not (0.0 <= position_mm <= self._stroke_mm):
                raise ValueError(f"position {position_mm}mm out of range 0..{self._stroke_mm}mm")
            counts = round(position_mm / self._stroke_mm * FULL_STROKE_COUNTS)
            await self._session.write_single_register(self._slave_id, GOAL_POSITION, counts)
        elif action == "stop":
            await self._session.write_single_register(self._slave_id, ACTUATOR_STOP, 1)
        elif action == "restart":
            await self._session.write_single_register(self._slave_id, RESTART, 1)
        else:
            raise ValueError(f"unknown action '{action}'")

    def is_action_complete(self, action: str, params: dict[str, Any]) -> bool:
        if action in ("move", "stop"):
            return not self._moving
        return True

    def snapshot(self) -> TargetState:
        age_s = self._clock.now() - self._observed_at
        quality: Quality
        if not self._connected:
            quality = "UNKNOWN"
        elif age_s < STALE_AFTER_S:
            quality = "GOOD"
        else:
            quality = "STALE"
        return TargetState(
            target_id=self.target_id,
            connection="CONNECTED" if self._connected else "DISCONNECTED",
            fields={
                "position": ObservedValue(
                    value=self._position_mm, observed_at=self._observed_at, quality=quality
                ),
                "moving": ObservedValue(
                    value=self._moving, observed_at=self._observed_at, quality=quality
                ),
                "alarm": ObservedValue(
                    value=self._alarm, observed_at=self._observed_at, quality=quality
                ),
            },
        )

    async def _poll_loop(self) -> None:
        while True:
            await self._poll_once()
            await self._clock.sleep(POLL_INTERVAL_S)

    async def _poll_once(self) -> None:
        try:
            registers = await self._session.read_holding_registers(
                self._slave_id, POLL_BLOCK_START, POLL_BLOCK_COUNT
            )
            self._position_mm = (
                registers[OFFSET_PRESENT_POSITION] / FULL_STROKE_COUNTS * self._stroke_mm
            )
            self._moving = registers[OFFSET_MOVING] != 0
            self._alarm = registers[OFFSET_HW_ERROR] != 0
            self._connected = True
        except Exception:  # noqa: BLE001 - a poll failure marks the Target disconnected, it doesn't crash the Runtime
            self._connected = False
        self._observed_at = self._clock.now()
