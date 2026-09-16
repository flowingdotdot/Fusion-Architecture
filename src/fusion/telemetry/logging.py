"""Structured logging setup (doc section 16): one rotating file per Runtime process,
tagged with runtime_id/runtime_boot_id on every record. UI row counts and in-memory
activity views are a separate concern (the Event stream already carries that data);
this module only owns "does a durable, size-bounded record exist on disk without a
UI open."
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


class _RuntimeContextFilter(logging.Filter):
    def __init__(self, runtime_id: str, runtime_boot_id: str) -> None:
        super().__init__()
        self._runtime_id = runtime_id
        self._runtime_boot_id = runtime_boot_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.runtime_id = self._runtime_id
        record.runtime_boot_id = self._runtime_boot_id
        return True


def configure_runtime_logging(
    app_name: str,
    runtime_id: str,
    runtime_boot_id: str,
    *,
    log_dir: Path | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configures the root "fusion" logger with a rotating file handler under
    ``log_dir`` (default: ``<cwd>/logs``, kept separate from any project/install path
    per doc section 17) plus a plain console handler. Safe to call once per process.
    """
    log_dir = log_dir or Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("fusion")
    logger.setLevel(level)
    # A Filter attached to this ("fusion") Logger object would NOT run for records
    # actually emitted by child loggers like "fusion.motor"/"fusion.scheduler" --
    # Python's propagation walk only re-checks each ancestor's *handlers*, not its
    # *filters*, once the record has already passed the originating logger's own
    # (empty) filter list. The filter has to live on the handlers instead, since
    # Handler.handle() does apply its own filters regardless of which logger the
    # record came from. (Found via a real KeyError: 'runtime_id' from every
    # non-"fusion"-root logger call once this was actually exercised.)
    context_filter = _RuntimeContextFilter(runtime_id, runtime_boot_id)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s runtime_id=%(runtime_id)s"
        " runtime_boot_id=%(runtime_boot_id)s %(name)s: %(message)s"
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{app_name}.log",
        maxBytes=DEFAULT_MAX_BYTES,
        backupCount=DEFAULT_BACKUP_COUNT,
        encoding="utf-8",
        delay=True,  # doc: file writes must not block the control loop at import/start time
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context_filter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)
    logger.addHandler(console_handler)

    return logger
