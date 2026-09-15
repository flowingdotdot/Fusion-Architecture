"""Motor UI entrypoint -- a separate process from the Motor Runtime (doc section 4).
Run a Motor Runtime first (``uv run fusion-motor``), then ``uv run fusion-motor-ui``.
Requires the "studio" dependency group (``uv sync --group studio``).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from fusion.ui.motor_window import MotorWindow

DEFAULT_URL = "http://127.0.0.1:8101"


def run() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    app = QApplication(sys.argv)
    window = MotorWindow(url)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
