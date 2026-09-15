"""Scheduler UI entrypoint. This one embeds the Scheduler Runtime directly in the UI
process (doc section 4 explicitly allows one launcher for both); only its calls out
to Motor go over the real transport layer. Run a Motor Runtime first (``uv run
fusion-motor``), then ``uv run fusion-scheduler-ui``. Requires the "studio"
dependency group (``uv sync --group studio``).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from fusion.telemetry.logging import configure_runtime_logging
from fusion.ui.scheduler_window import SchedulerWindow

DEFAULT_MOTOR_URL = "http://127.0.0.1:8101"


def run() -> None:
    motor_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MOTOR_URL
    configure_runtime_logging("scheduler-ui", "scheduler-ui", "boot-ui")
    app = QApplication(sys.argv)
    window = SchedulerWindow(motor_url)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
