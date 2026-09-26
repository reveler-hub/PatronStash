"""What `patronstash run` shows while it works.

- PostReporter logs a line for each post that had new files, plus a
  heartbeat during long backfills, so the log shows progress too.
- ProgressBar is a gallery-dl output object: on a terminal it draws a live
  one-line progress bar for the file being downloaded. Under cron or
  systemd (no terminal) it stays silent.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time

from .fmt import human_size, plural

log = logging.getLogger("patronstash")

HEARTBEAT_EVERY = 50  # posts
BAR_WIDTH = 10
REDRAW_INTERVAL = 0.25  # seconds

_active_bar: ProgressBar | None = None


def clear_status_line() -> None:
    """Erase the progress bar, if one is showing, so a log line can print."""
    if _active_bar is not None:
        _active_bar.clear()


class PostReporter:
    def __init__(self, creator: str, heartbeat_every: int = HEARTBEAT_EVERY):
        self.creator = creator
        self.heartbeat_every = heartbeat_every
        self.checked = 0
        self.new_files = 0
        self._post: str | None = None
        self._post_files = 0

    def post_started(self, kwdict: dict) -> None:
        self.finish_post()
        date = kwdict.get("date")
        day = f"{date:%Y-%m-%d} " if date else ""
        title = (kwdict.get("title") or "").strip() or "Untitled"
        self._post = f"{day}{title[:80]}"
        self._post_files = 0
        self.checked += 1
        if self.checked % self.heartbeat_every == 0:
            log.info(
                "%s: %s checked, %s so far…",
                self.creator,
                plural(self.checked, "post"),
                plural(self.new_files, "new file"),
            )

    def file_done(self) -> None:
        self._post_files += 1
        self.new_files += 1

    def finish_post(self) -> None:
        if self._post is not None and self._post_files:
            log.info(
                "%s: %s — %s",
                self.creator,
                self._post,
                plural(self._post_files, "file"),
            )
        self._post = None


class ProgressBar:
    """A gallery-dl output object that draws a progress bar on a terminal."""

    def __init__(self, stream=None, enabled: bool | None = None, clock=time.monotonic):
        self.stream = stream if stream is not None else sys.stderr
        if enabled is None:
            enabled = hasattr(self.stream, "isatty") and self.stream.isatty()
        self.enabled = enabled
        self.clock = clock
        self.name = ""
        self.shown = False
        self._last_draw = None

    def __enter__(self):
        global _active_bar
        _active_bar = self
        return self

    def __exit__(self, *exc):
        global _active_bar
        self.clear()
        _active_bar = None

    # gallery-dl's output interface

    def start(self, path):
        self.name = os.path.basename(path)
        self._last_draw = None

    def skip(self, path):
        self.clear()

    def success(self, path):
        self.clear()

    def progress(self, bytes_total, bytes_downloaded, bytes_per_second):
        if not self.enabled:
            return
        now = self.clock()
        if self._last_draw is not None and now - self._last_draw < REDRAW_INTERVAL:
            return
        self._last_draw = now
        self._write(
            "\r\033[K" + self.line(bytes_total, bytes_downloaded, bytes_per_second)
        )
        self.shown = True

    def line(self, total, done, rate) -> str:
        speed = f"{human_size(rate)}/s"
        if total:
            fraction = min(done / total, 1.0)
            filled = int(fraction * BAR_WIDTH)
            bar = "#" * filled + "-" * (BAR_WIDTH - filled)
            text = (
                f"  {self.name}  [{bar}] {int(fraction * 100):>3}%  "
                f"{human_size(done)} / {human_size(total)}  {speed}"
            )
        else:
            text = f"  {self.name}  {human_size(done)}  {speed}"
        width = shutil.get_terminal_size((100, 20)).columns - 1
        return text[:width]

    def clear(self):
        if self.shown:
            self._write("\r\033[K")
            self.shown = False

    def _write(self, text):
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            self.enabled = False
