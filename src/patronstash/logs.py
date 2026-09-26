"""Logging: a quiet console plus a rotating log file in data_dir."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .progress import clear_status_line

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_FILES = 5
FILE_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


class _ConsoleFilter(logging.Filter):
    """Quiet mode: PatronStash's own summary lines, plus anyone's warnings."""

    def __init__(self, verbose: bool):
        super().__init__()
        self.verbose = verbose

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "console", True):
            return False
        if record.levelno >= logging.WARNING:
            return True
        if record.levelno >= logging.INFO:
            return self.verbose or record.name == "patronstash"
        return False


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        prefix = "" if record.name == "patronstash" else f"[{record.name}] "
        if record.levelno >= logging.ERROR:
            return f"{prefix}error: {message}"
        if record.levelno >= logging.WARNING:
            return f"{prefix}warning: {message}"
        return f"{prefix}{message}"


class _ConsoleHandler(logging.StreamHandler):
    def emit(self, record):
        clear_status_line()  # don't print on top of a progress bar
        super().emit(record)


def setup_console(verbose: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    console = _ConsoleHandler(sys.stderr)
    console.addFilter(_ConsoleFilter(verbose))
    console.setFormatter(_ConsoleFormatter())
    root.addHandler(console)


def add_log_file(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "patronstash.log"
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_FILES - 1, encoding="utf-8"
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(FILE_FORMAT))
    logging.getLogger().addHandler(handler)
    return path
