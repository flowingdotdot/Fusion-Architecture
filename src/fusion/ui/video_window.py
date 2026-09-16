"""Minimal Video control UI (doc section 19 stage 5 follow-up): a separate process
that connects to an already-running Video Runtime over HTTP -- closing this window
never touches playback (the Runtime owns the actual output window/mpv instance).

Includes a small live preview thumbnail, polled from the Runtime's ``/preview``
route (a JPEG snapshot of the current frame) rather than a second full render
pipeline -- good enough for "am I showing the right thing," not meant to replace
looking at the real output.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from fusion.contracts.ids import new_id
from fusion.transport.client import RuntimeClient
from fusion.ui.asyncio_bridge import AsyncioBridge

POLL_INTERVAL_MS = 500
PREVIEW_INTERVAL_MS = 500
PREVIEW_WIDTH = 240


class VideoWindow(QMainWindow):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.setWindowTitle(f"Fusion Video -- {base_url}")
        self.resize(560, 560)

        self._client = RuntimeClient(base_url)
        self._bridge = AsyncioBridge()
        self._session_id: str | None = None
        self._targets: list[dict[str, Any]] = []
        self._preview_busy = False

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self._status_label = QLabel("connecting...")
        layout.addWidget(self._status_label)

        preview_row = QHBoxLayout()
        self._preview_label = QLabel("(no preview yet)")
        self._preview_label.setFixedSize(PREVIEW_WIDTH, PREVIEW_WIDTH * 9 // 16)
        self._preview_label.setStyleSheet("background-color: #222; color: #888;")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_row.addWidget(self._preview_label)

        state_col = QVBoxLayout()
        self._target_state_label = QLabel("-")
        self._target_state_label.setWordWrap(True)
        state_col.addWidget(self._target_state_label)
        preview_row.addLayout(state_col, stretch=1)
        layout.addLayout(preview_row)

        session_row = QHBoxLayout()
        self._acquire_btn = QPushButton("Acquire Manual Session")
        self._acquire_btn.clicked.connect(self._on_acquire_session)
        session_row.addWidget(self._acquire_btn)
        self._session_label = QLabel("no session")
        session_row.addWidget(self._session_label)
        layout.addLayout(session_row)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target:"))
        self._target_combo = QComboBox()
        target_row.addWidget(self._target_combo)
        layout.addLayout(target_row)

        prepare_row = QHBoxLayout()
        self._media_input = QLineEdit()
        self._media_input.setPlaceholderText("media path (e.g. C:/videos/a.mp4)")
        prepare_row.addWidget(self._media_input)
        self._prepare_btn = QPushButton("Prepare")
        self._prepare_btn.clicked.connect(self._on_prepare)
        prepare_row.addWidget(self._prepare_btn)
        layout.addLayout(prepare_row)

        transport_row = QHBoxLayout()
        self._play_btn = QPushButton("Play")
        self._play_btn.clicked.connect(self._on_play)
        transport_row.addWidget(self._play_btn)
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._on_pause)
        transport_row.addWidget(self._pause_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop)
        transport_row.addWidget(self._stop_btn)
        self._loop_checkbox = QCheckBox("Loop")
        self._loop_checkbox.stateChanged.connect(self._on_loop_toggled)
        transport_row.addWidget(self._loop_checkbox)
        layout.addLayout(transport_row)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Volume:"))
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.sliderReleased.connect(self._on_volume_changed)
        volume_row.addWidget(self._volume_slider)
        layout.addLayout(volume_row)

        layout.addWidget(QLabel("Log:"))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_snapshot)
        self._poll_timer.start(POLL_INTERVAL_MS)

        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._poll_preview)
        self._preview_timer.start(PREVIEW_INTERVAL_MS)

        self._bridge.run(self._client.get_info, self._on_info_done)
        self._bridge.run(self._client.get_targets, self._on_targets_done)

    def _append_log(self, text: str) -> None:
        self._log.appendPlainText(text)

    def _current_target_id(self) -> str | None:
        text = self._target_combo.currentText()
        return text or None

    # ---- async result handlers (run on the Qt thread) ----

    def _on_info_done(self, result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._status_label.setText(f"disconnected: {exc}")
            return
        self._status_label.setText(
            f"connected: {result['runtime_id']} (boot {result['runtime_boot_id']})"
        )

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
        target_id = self._current_target_id()
        state = result.get("targets", {}).get(target_id) if target_id else None
        if state is None:
            self._target_state_label.setText("no target selected")
            return
        fields = state.get("fields", {})
        lines = [f"{name}={field['value']}" for name, field in fields.items()]
        self._target_state_label.setText("\n".join(lines))

    def _poll_preview(self) -> None:
        target_id = self._current_target_id()
        if target_id is None or self._preview_busy:
            return
        self._preview_busy = True
        self._bridge.run(
            lambda: self._client.get_bytes(f"/api/v1/targets/{target_id}/preview"),
            self._on_preview_done,
        )

    def _on_preview_done(self, result: Any, exc: BaseException | None) -> None:
        self._preview_busy = False
        if exc is not None:
            return  # preview is best-effort; a Fake player has no /preview route at all
        pixmap = QPixmap()
        if pixmap.loadFromData(result):
            self._preview_label.setPixmap(
                pixmap.scaled(
                    self._preview_label.width(),
                    self._preview_label.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _on_acquire_session(self) -> None:
        self._bridge.run(
            lambda: self._client.acquire_session("video-ui", mode="MANUAL"), self._on_session_done
        )

    def _on_session_done(self, result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._append_log(f"session error: {exc}")
            return
        self._session_id = result["session_id"]
        self._session_label.setText(f"session {self._session_id}")
        self._append_log(f"control session acquired: {self._session_id}")

    def _submit(
        self, action: str, params: dict[str, Any] | None = None, completion: str = "acknowledged"
    ) -> None:
        if self._session_id is None:
            self._append_log("acquire a session first")
            return
        target_id = self._current_target_id()
        if target_id is None:
            self._append_log("no target selected")
            return
        self._bridge.run(
            lambda: self._client.submit_command(
                request_id=new_id(f"ui-{action}"),
                control_session_id=self._session_id,
                target_id=target_id,
                action=action,
                params=params or {},
                completion_requirement=completion,
            ),
            self._on_command_submitted,
        )

    def _on_prepare(self) -> None:
        media_path = self._media_input.text().strip()
        if not media_path:
            self._append_log("enter a media path first")
            return
        self._submit("prepare", {"media_path": media_path}, completion="observed")

    def _on_play(self) -> None:
        self._submit("play")

    def _on_pause(self) -> None:
        self._submit("pause")

    def _on_stop(self) -> None:
        self._submit("stop")

    def _on_loop_toggled(self, _state: int) -> None:
        self._submit("loop", {"enabled": self._loop_checkbox.isChecked()})

    def _on_volume_changed(self) -> None:
        self._submit("volume", {"level": float(self._volume_slider.value())})

    def _on_command_submitted(self, result: Any, exc: BaseException | None) -> None:
        if exc is not None:
            self._append_log(f"command error: {exc}")
            return
        self._append_log(
            f"command {result['command_id']}: {result['status']} / {result.get('outcome')}"
        )

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt's own method naming
        self._poll_timer.stop()
        self._preview_timer.stop()
        self._bridge.run(self._client.aclose, lambda _r, _e: None)
        self._bridge.stop()
        super().closeEvent(event)
