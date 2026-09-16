"""Video control UI entrypoint -- a separate process from the Video Runtime (doc
section 4). Run a Video Runtime first (``uv run fusion-video`` or ``uv run
fusion-video-mpv``), then ``uv run fusion-video-ui [url]``.
Requires the "studio" dependency group (``uv sync --group studio``).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from fusion.ui.video_window import VideoWindow

DEFAULT_URL = "http://127.0.0.1:8104"


def run() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    app = QApplication(sys.argv)
    window = VideoWindow(url)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
