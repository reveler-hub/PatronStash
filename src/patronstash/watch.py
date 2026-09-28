"""`patronstash watch`: keep running, do a pass every few hours, and show a
live dashboard of every creator.

The Watcher holds what the dashboard shows. It is fed by the engine through
three hooks (the downloader passed to runner.run, a progress object passed
to gdl.run_creator, and a log handler), so none of it needs curses and it
can be tested on its own. run_watch() adds the curses screen from tui.py.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from . import gdl, runner
from .config import Config, ConfigError, load_config
from .fmt import utcnow
from .notify import Notifier
from .stats import StatsDB
from .tui import ActiveDownload, CreatorRow, DashboardState

log = logging.getLogger("patronstash")

DEFAULT_EVERY_HOURS = 6.0
# How often watch looks for a new release. GitHub itself is still asked at
# most once a day (updates.py remembers the answer).
UPDATE_CHECK_EVERY = 3600


class _DashboardProgress:
    """Takes the place of the terminal progress bar (see gdl.run_creator)."""

    def __init__(self, watcher: Watcher):
        self.watcher = watcher
        self.name = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.watcher._set_active(None)

    def start(self, path):
        self.name = Path(path).name
        self.watcher._set_active(self.name, 0, None, 0)

    def progress(self, total, done, speed):
        self.watcher._set_active(self.name, done, total, speed)

    def success(self, path):
        self.watcher._set_active(None)

    skip = clear = success


class _DashboardLog(logging.Handler):
    """Shows the engine's important messages on the dashboard."""

    def __init__(self, watcher: Watcher):
        super().__init__(level=logging.INFO)
        self.watcher = watcher

    def emit(self, record):
        message = record.getMessage().splitlines()[0]
        if " is available (you have " in message:
            available = message.split(" Update with:")[0]
            self.watcher._set_update(f"⬆ {available} See the README to update.")
        elif "already running" in message:
            self.watcher._set_notice(
                "Another PatronStash run is going; this pass was skipped"
            )
        elif record.levelno >= logging.ERROR and record.name == "patronstash":
            self.watcher._error(message)


class Watcher:
    def __init__(
        self,
        config_path: Path,
        *,
        every: float,
        clock=time.time,
        run=runner.run,
        fetch_tags=None,
    ):
        self.config_path = Path(config_path)
        self.every = every
        self.clock = clock
        self.run = run
        self.fetch_tags = fetch_tags
        self.cfg: Config | None = None
        self._lock = threading.Lock()
        self._rows: dict[str, CreatorRow] = {}
        self._active: ActiveDownload | None = None
        self._current: str | None = None
        self._done = 0
        self._total = 0
        self._running = False
        self._notice = ""  # problems with the current pass; cleared each pass
        self._update = ""  # a newer release; kept until watch restarts
        self._next_pass_at = 0.0  # the first pass runs straight away
        self._run_now = threading.Event()

    # ── config and rows ─────────────────────────────────────────────

    def reload(self) -> Config | None:
        """Read the config again; on a problem, keep watching and say why."""
        try:
            self.cfg = load_config(self.config_path)
        except ConfigError as exc:
            self._set_notice(f"Config problem: {exc}")
            return None
        return self.cfg

    def refresh_rows(self) -> None:
        """One row per configured creator, with what's already archived."""
        cfg = self.cfg
        if cfg is None:
            return
        try:
            with StatsDB(cfg.data_dir / "stats.db") as stats:
                summaries = {c.name: stats.file_summary(c.name) for c in cfg.creators}
                states = {c.name: stats.get_creator(c.name) for c in cfg.creators}
        except Exception:
            summaries, states = {}, {}
        rows = {}
        for creator in cfg.creators:
            old = self._rows.get(creator.name)
            row = old or CreatorRow(creator.name, "waiting", "Waiting")
            s = summaries.get(creator.name)
            if s is not None:
                row.posts, row.size = s.posts, s.bytes
                row.newest = f"{s.last_post_date:%Y-%m-%d}" if s.last_post_date else "-"
            state = states.get(creator.name)
            if old is None:
                if state is not None and state.last_result == "ok":
                    row.kind, row.status = "ok", "✓ Up to date"
                    if not state.complete:
                        row.kind, row.status = "waiting", "Backfilling"
                row.activity = (
                    ("backfill in progress" if state and not state.complete else "")
                    if s and s.files
                    else "nothing archived yet"
                )
            rows[creator.name] = row
        for name in cfg.missing_backfill:
            rows[name] = CreatorRow(
                name, "error", "✗ Skipped", activity="no backfill line in the config"
            )
        with self._lock:
            self._rows = rows

    # ── hooks the engine calls ──────────────────────────────────────

    def downloader(self, cfg, creator, **kwargs):
        """Wraps gdl.run_creator for runner.run."""
        name = creator.name
        record = kwargs.pop("on_file")
        count = 0
        self._set_row(name, "active", "… Checking", "")
        with self._lock:
            self._current = name

        new_posts: set[str] = set()

        def on_file(f):
            nonlocal count
            count += 1
            self._set_row(name, "active", f"↓ {count} file{'s' if count != 1 else ''}")
            with self._lock:  # count the file straight away, not at the end
                row = self._rows[name]
                row.size += f.size
                if f.post_id not in new_posts:
                    new_posts.add(f.post_id)
                    row.posts += 1
                if f.post_date is not None:
                    day = f"{f.post_date:%Y-%m-%d}"
                    row.newest = max(row.newest, day) if row.newest != "-" else day
            record(f)

        try:
            result = gdl.run_creator(
                cfg,
                creator,
                on_file=on_file,
                progress=_DashboardProgress(self),
                **kwargs,
            )
        except Exception:
            self._set_row(name, "error", "✗ Error", "unexpected error, see the log")
            raise
        finally:
            with self._lock:
                self._current = None
                self._active = None
                self._done += 1
        summary = runner.summary_line(result)
        if result.ok:
            status = f"✓ {result.new_posts} new" if result.files else "✓ Up to date"
            self._set_row(name, "ok", status, summary)
        else:
            with self._lock:
                already = self._rows.get(name) and self._rows[name].kind == "error"
            if not already:
                self._set_row(name, "error", "✗ Error", summary + "; see the log")
        return result

    def log_handler(self) -> logging.Handler:
        return _DashboardLog(self)

    # ── passes ──────────────────────────────────────────────────────

    def run_pass(self) -> int | None:
        cfg = self.reload()
        if cfg is None:
            return None
        self._set_notice("")
        self.refresh_rows()
        with self._lock:
            self._running = True
            self._done, self._total = 0, len(cfg.creators)
            for creator in cfg.creators:
                row = self._rows[creator.name]
                if row.kind != "error":
                    row.kind, row.status = "waiting", "Waiting"
        try:
            return self.run(
                cfg, notifier=Notifier(cfg.notify_url), downloader=self.downloader
            )
        except Exception:
            log.exception("pass failed")
            return 1
        finally:
            with self._lock:
                self._running = False
                self._active = None
            self.refresh_rows()

    def tick(self) -> None:
        """Run a pass if one is due or was asked for."""
        if self.clock() >= self._next_pass_at or self._run_now.is_set():
            self._run_now.clear()
            self.run_pass()
            self._next_pass_at = self.clock() + self.every

    def check_updates(self) -> None:
        """Look for a newer release, whether or not a pass is running.

        run_watch calls this at start-up and then every UPDATE_CHECK_EVERY
        seconds from its own thread, so a long pass doesn't delay it. The
        notice arrives through the log handler; the notification is sent at
        most once per version, as with `run`.
        """
        cfg = self.cfg or self.reload()
        if cfg is None:
            return
        try:
            runner.report_update(
                cfg, Notifier(cfg.notify_url), utcnow(), self.fetch_tags
            )
        except Exception as exc:  # never let the update check stop watch
            log.debug("update check failed: %s", exc)

    def on_key(self, key: int) -> None:
        if key in (ord("r"), ord("R")):
            self._run_now.set()

    # ── what the dashboard shows ────────────────────────────────────

    def state(self) -> DashboardState:
        with self._lock:
            running = self._running
            return DashboardState(
                rows=[CreatorRow(**vars(r)) for r in self._rows.values()],
                active=self._active,
                checking=self._current if self._active is None else None,
                next_pass_in=None
                if running
                else max(0, int(self._next_pass_at - self.clock())),
                pass_progress=(self._done, self._total) if running else None,
                notice=" • ".join(n for n in (self._notice, self._update) if n),
            )

    # ── internals ───────────────────────────────────────────────────

    def _set_row(self, name, kind, status, activity=None):
        with self._lock:
            row = self._rows.setdefault(name, CreatorRow(name, kind, status))
            row.kind, row.status = kind, status
            if activity is not None:
                row.activity = activity

    def _set_active(self, filename, done=0, total=None, speed=0):
        with self._lock:
            if filename is None or self._current is None:
                self._active = None
            else:
                self._active = ActiveDownload(
                    self._current, filename, done, total, speed
                )

    def _set_notice(self, text: str) -> None:
        with self._lock:
            self._notice = text

    def _set_update(self, text: str) -> None:
        with self._lock:
            self._update = text

    def _error(self, message: str) -> None:
        with self._lock:
            current = self._current
        if current and message.startswith(current + ":"):
            self._set_row(
                current, "error", "✗ Error", message[len(current) + 1 :].strip()
            )
        elif current is None:
            self._set_notice(message)


def run_watch(config_path: Path, every_hours: float = DEFAULT_EVERY_HOURS) -> int:
    """Run passes every `every_hours` hours with a live dashboard, until q."""
    from .logs import add_log_file
    from .tui import run_dashboard

    watcher = Watcher(config_path, every=max(every_hours, 0.25) * 3600)
    cfg = watcher.reload()
    if cfg is None:
        print(watcher.state().notice, file=sys.stderr)
        return 2

    # Nothing may print over the dashboard: the log goes to its file, and
    # anything printed directly (yt-dlp, gallery-dl) to watch-output.log.
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    for handler in saved_handlers:
        root.removeHandler(handler)
    add_log_file(cfg.data_dir)
    root.addHandler(watcher.log_handler())
    saved_streams = sys.stdout, sys.stderr
    output = open(cfg.data_dir / "watch-output.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = output
    log.info("watch started (every %s h)", every_hours)

    stop = threading.Event()

    def loop():
        while not stop.is_set():
            watcher.tick()
            stop.wait(0.5)

    def update_loop():
        while not stop.is_set():
            watcher.check_updates()
            stop.wait(UPDATE_CHECK_EVERY)

    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    try:
        run_dashboard(watcher.state, on_key=watcher.on_key)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        log.info("watch stopped")
        sys.stdout, sys.stderr = saved_streams
        output.close()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        gdl.close_api_extractors()
    return 0
