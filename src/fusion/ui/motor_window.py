"""Minimal Motor UI (doc section 19 stage 3): a separate process that connects to an
already-running Motor Runtime over HTTP -- closing this window never touches the
Runtime process. Manual control only (acquire a MANUAL session, move/stop one
target); no Timeline/Trigger editing here, that lives in the Scheduler.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fusion.contracts.ids import new_id
from fusion.transport.client import RuntimeClient
from fusion.ui.asyncio_bridge import AsyncioBridge

POLL_INTERVAL_MS = 500


class MotorWindow(QMainWindow):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.setWindowTitle(f"Fusion Motor -- {base_url}")
        self.resize(560, 420)

        self._client = RuntimeClient(base_url)
        self._bridge = AsyncioBridge()
        self._session_id: str | None = None
        self._targets: list[dict[str, Any]] = []

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self._status_label = QLabel("connecting...")
        layout.addWidget(self._status_label)

        self._target_state_label = QLabel("-")
        self._target_state_label.setWordWrap(True)
        layout.addWidget(self._target_state_label)

        session_row = QHBoxLayout()
        self._acquire_btn = QPushButton("Acquire Manual Session")
        self._acquire_btn.clicked.connect(self._on_acquire_session)
        session_row.addWidget(self._acquire_btn)
        self._session_label = QLabel("no session")
        session_row.addWidget(self._session_label)
        layout.addLayout(session_row)

        control_row = QHBoxLayout()
        control_row.addWidget(QLabel("Target:"))
        self._target_combo = QComboBox()
        control_row.addWidget(self._target_combo)
        control_row.addWidget(QLabel("Position:"))
        self._position_input = QLineEdit("0")
        self._position_input.setFixedWidth(80)
        control_row.addWidget(self._position_input)
        self._move_btn = QPushButton("Move")
        self._move_btn.clicked.connect(self._on_move)
        control_row.addWidget(self._move_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop)
        control_row.addWidget(self._stop_btn)
        layout.addLayout(control_row)

        layout.addWidget(QLabel("Log:"))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_snapshot)
        self._poll_timer.start(POLL_INTERVAL_MS)

        self._bridge.run(self._client.get_info, self._on_info_done)
        self._bridge.run(self._client.get_targets, self._on_targets_done)

    def _append_log(self, text: str) -> None:
        self._log.appendPlainText(text)

    # ---- async result handlers (run on the Qt thread) ----

    def _on_info_done(self, result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._status_label.setText(f"disconnected: {exc}")
            return
        self._status_label.setText(
            f"connected: {result['runtime_id']} (boot {result['runtime_boot_id']})"
        )
        self._append_log(f"info: {result}")

    def _on_targets_done(self, result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._append_log(f"could not fetch targets: {exc}")
            return
        self._targets = result
        self._target_combo.clear()
        self._target_combo.addItems([t["target_id"] for t in result])

    def _poll_snapshot(self) -> None:
        self._bridge.run(self._client.get_snapshot, self._on_snapshot_done)

    def _on_snapshot_done(self, result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._status_label.setText(f"disconnected: {exc}")
            return
        lines = []
        for target_id, state in result.get("targets", {}).items():
            fields = state.get("fields", {})
            rendered = ", ".join(f"{name}={field['value']}" for name, field in fields.items())
            lines.append(f"{target_id}: {rendered}")
        self._target_state_label.setText("\n".join(lines) or "no targets")

    def _on_acquire_session(self) -> None:
        self._bridge.run(
            lambda: self._client.acquire_session("motor-ui", mode="MANUAL"), self._on_session_done
        )

    def _on_session_done(self, result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._append_log(f"session error: {exc}")
            return
        self._session_id = result["session_id"]
        self._session_label.setText(f"session {self._session_id}")
        self._append_log(f"control session acquired: {self._session_id}")

    def _selected_target(self) -> dict[str, Any] | None:
        target_id = self._target_combo.currentText()
        return next((t for t in self._targets if t["target_id"] == target_id), None)

    def _on_move(self) -> None:
        if self._session_id is None:
            self._append_log("acquire a session first")
            return
        target = self._selected_target()
        if target is None:
            self._append_log("no target selected")
            return
        try:
            position = float(self._position_input.text())
        except ValueError:
            self._append_log("invalid position")
            return
        self._bridge.run(
            lambda: self._client.submit_command(
                request_id=new_id("ui-move"),
                control_session_id=self._session_id,
                target_id=target["target_id"],
                action="move",
                params={"position": position},
            ),
            self._on_command_submitted,
        )

    def _on_stop(self) -> None:
        if self._session_id is None:
            self._append_log("acquire a session first")
            return
        target = self._selected_target()
        if target is None:
            self._append_log("no target selected")
            return
        stop_action = next((a for a in target["actions"] if a["priority"] == "control"), None)
        if stop_action is None:
            self._append_log(f"target '{target['target_id']}' has no stop-priority action")
            return
        self._bridge.run(
            lambda: self._client.submit_command(
                request_id=new_id("ui-stop"),
                control_session_id=self._session_id,
                target_id=target["target_id"],
                action=stop_action["name"],
                completion_requirement="acknowledged",
            ),
            self._on_command_submitted,
        )

    def _on_command_submitted(self, result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._append_log(f"command error: {exc}")
            return
        self._append_log(
            f"command {result['command_id']}: {result['status']} / {result.get('outcome')}"
        )

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt's own method naming
        self._poll_timer.stop()
        self._bridge.run(self._client.aclose, lambda _r, _e: None)
        self._bridge.stop()
        super().closeEvent(event)
