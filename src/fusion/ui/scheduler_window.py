"""Minimal Scheduler UI (doc section 19 stage 3): per doc section 4 ("하나의
Launcher나 EXE 실행 모드로 UI와 Runtime을 시작해도 된다"), this embeds the headless
SchedulerRuntimeApp directly in the UI process rather than talking to it over a
network -- there is no separate Scheduler Runtime server yet (deferred until a
second consumer actually needs one). It still only ever reaches Motor over the real
transport layer (HTTP/WS), never in-process.

"Start" is wired through the TriggerEngine (a FakeInputSource standing in for a real
button/sensor, per doc section 19 stage 3's own scope note) so its debounce/cooldown
protection is visibly exercised, not just unit-tested. Hold/Abort are direct
operator commands (doc's own diagram shows "Scheduler UI --> SC" as a direct path).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fusion.apps.scheduler.runtime import SchedulerRuntimeApp
from fusion.contracts.timeline import Cue, Timeline
from fusion.contracts.trigger import Trigger, TriggerCondition
from fusion.core.clock import RealClock
from fusion.core.show_controller import ShowState
from fusion.simulation.fake_input import FakeInputSource
from fusion.transport.client import RuntimeClient
from fusion.ui.asyncio_bridge import AsyncioBridge

POLL_INTERVAL_MS = 200

DEMO_TIMELINE = Timeline(
    cues=[
        Cue(
            cue_id="cue-1",
            at_ms=0,
            runtime="motor",
            target_id="motor01",
            action="move",
            params={"position": 100},
        ),
        Cue(
            cue_id="cue-2",
            at_ms=200,
            runtime="motor",
            target_id="motor01",
            action="move",
            params={"position": 300},
            depends_on=["cue-1"],
        ),
    ]
)


class SchedulerWindow(QMainWindow):
    def __init__(self, motor_url: str) -> None:
        super().__init__()
        self.setWindowTitle("Fusion Scheduler")
        self.resize(560, 420)

        self._bridge = AsyncioBridge()
        self._clock = RealClock()
        self._client = RuntimeClient(motor_url)
        self._scheduler = SchedulerRuntimeApp({"motor": self._client}, self._clock)
        self._scheduler.load_timeline(DEMO_TIMELINE)
        self._scheduler.register_trigger(
            Trigger(
                trigger_id="ui-start",
                condition=TriggerCondition(source="ui", type="start"),
                action="start",
                show_states=[ShowState.ARMED.value],
                cooldown_ms=500,
            )
        )
        self._input = FakeInputSource(self._scheduler.triggers, self._clock, source="ui")
        self._scheduler.triggers.on_action_error = lambda _trigger, exc: self._append_log(
            f"trigger rejected: {exc}"
        )

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self._motor_status_label = QLabel("connecting to motor...")
        layout.addWidget(self._motor_status_label)
        layout.addWidget(
            QLabel("Timeline: cue-1 (move -> 100) then cue-2 (move -> 300, depends on cue-1)")
        )

        button_row = QHBoxLayout()
        self._arm_btn = QPushButton("Arm")
        self._arm_btn.clicked.connect(self._on_arm)
        self._start_btn = QPushButton("Start (via Trigger)")
        self._start_btn.clicked.connect(self._on_start)
        self._hold_btn = QPushButton("Hold")
        self._hold_btn.clicked.connect(self._on_hold)
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.clicked.connect(self._on_abort)
        for button in (self._arm_btn, self._start_btn, self._hold_btn, self._abort_btn):
            button_row.addWidget(button)
        layout.addLayout(button_row)

        self._show_status_label = QLabel("-")
        layout.addWidget(self._show_status_label)

        layout.addWidget(QLabel("Log:"))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_status)
        self._poll_timer.start(POLL_INTERVAL_MS)

        self._bridge.run(self._client.get_info, self._on_info_done)

    def _append_log(self, text: str) -> None:
        self._log.appendPlainText(text)

    def _on_info_done(self, result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._motor_status_label.setText(f"motor unreachable: {exc}")
            return
        self._motor_status_label.setText(
            f"motor: {result['runtime_id']} (boot {result['runtime_boot_id']})"
        )

    def _on_arm(self) -> None:
        self._bridge.run(self._scheduler.arm, self._on_arm_done)

    def _on_arm_done(self, _result: Any, exc: BaseException | None) -> None:
        self._append_log(f"arm failed: {exc}" if exc is not None else "armed")

    def _on_start(self) -> None:
        self._bridge.run(lambda: self._input.press("start"), self._on_start_done)

    def _on_start_done(self, _result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._append_log(f"start error: {exc}")

    def _on_hold(self) -> None:
        try:
            self._scheduler.hold()
            self._append_log("held")
        except RuntimeError as exc:
            self._append_log(f"hold rejected: {exc}")

    def _on_abort(self) -> None:
        self._bridge.run(self._scheduler.abort, self._on_abort_done)

    def _on_abort_done(self, _result: Any, exc: BaseException | None) -> None:
        self._append_log(f"abort failed: {exc}" if exc is not None else "aborted")

    def _refresh_status(self) -> None:
        status = self._scheduler.status()
        self._show_status_label.setText(str(status))

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt's own method naming
        self._poll_timer.stop()
        self._bridge.run(self._client.aclose, lambda _r, _e: None)
        self._bridge.stop()
        super().closeEvent(event)
