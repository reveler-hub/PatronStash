"""`patronstash run`: one pass over every creator."""

from __future__ import annotations

import logging
import shutil

from . import gdl
from .config import Config
from .fmt import human_size, plural, utcnow
from .lock import AlreadyRunning, run_lock
from .stats import StatsDB

log = logging.getLogger("patronstash")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _summary_line(result: gdl.CreatorRun) -> str:
    if result.files:
        text = (
            f"{plural(result.new_posts, 'new post')}, "
            f"{plural(len(result.files), 'file')} ({human_size(result.new_bytes)})"
        )
    else:
        text = "nothing new"
    counts = result.counts
    if counts.locked:
        text += f"; {plural(counts.locked, 'locked post')} skipped"
    if counts.videos_skipped:
        text += f"; {plural(counts.videos_skipped, 'video')} skipped (no ffmpeg)"
    if result.public_links_skipped:
        text += (
            f"; {plural(result.public_links_skipped, 'public YouTube link')} left alone"
        )
    if result.video_failures:
        text += f"; {plural(len(result.video_failures), 'video')} failed"
    return text


def run(
    cfg: Config,
    *,
    notifier,
    verbose: bool = False,
    now=utcnow,
    login_checker=gdl.check_login,
    downloader=gdl.run_creator,
    ffmpeg_available=ffmpeg_available,
) -> int:
    """Download everything new for every creator. Returns an exit code."""
    try:
        with run_lock(cfg.data_dir / "patronstash.lock"):
            return _run(
                cfg, notifier, verbose, now, login_checker, downloader, ffmpeg_available
            )
    except AlreadyRunning:
        log.info("PatronStash is already running; exiting")
        return 0


def _run(
    cfg, notifier, verbose, now, login_checker, downloader, ffmpeg_available
) -> int:
    for warning in cfg.warnings:
        log.warning("config: %s", warning)

    if cfg.missing_backfill:
        names = ", ".join(cfg.missing_backfill)
        log.warning(
            "skipping %s: no `backfill` line in the config (%s)",
            plural(len(cfg.missing_backfill), "creator"),
            names,
        )
        notifier.send(
            "PatronStash: creator skipped",
            f"No `backfill` line for: {names}. Add one to {cfg.path} "
            "to start archiving them.",
        )

    if not cfg.creators:
        log.warning("no creators to archive; add [[creator]] blocks to %s", cfg.path)
        return 0

    login = login_checker(cfg)
    if not login.ok:
        log.error("login failed: %s", login.message)
        notifier.send(
            "PatronStash: login failed",
            f"Nothing was downloaded: {login.message}.",
        )
        return 1
    log.info("login: %s", login.message)

    allow_video = ffmpeg_available()
    if not allow_video:
        log.warning("ffmpeg not found: skipping video; everything else still downloads")

    failures: list[str] = []
    new_posts = new_files = 0
    with StatsDB(cfg.data_dir / "stats.db") as stats:
        for creator in cfg.creators:
            state = stats.begin_creator(creator.name, creator.backfill, now())

            def record(f, name=creator.name):
                stats.record_file(name, f.post_id, f.post_date, f.path, f.size, now())

            log.info("%s: checking for new posts…", creator.name)
            try:
                result = downloader(
                    cfg,
                    creator,
                    cutoff=state.cutoff,
                    complete=state.complete,
                    allow_video=allow_video,
                    verbose=verbose,
                    on_file=record,
                )
            except Exception as exc:
                log.exception("%s: unexpected error", creator.name)
                failures.append(f"{creator.name}: {exc.__class__.__name__}: {exc}")
                stats.finish_creator(
                    creator.name, "error", now(), backfill_complete=False
                )
                continue

            new_posts += result.new_posts
            new_files += len(result.files)
            stats.finish_creator(
                creator.name,
                "ok" if result.ok else "error",
                now(),
                backfill_complete=result.ok and not result.counts.videos_skipped,
                new_posts=result.new_posts,
                new_files=len(result.files),
                locked=result.counts.locked,
            )
            if result.ok:
                log.info("%s: %s", creator.name, _summary_line(result))
            else:
                log.error(
                    "%s: FAILED (%s); see the log for details",
                    creator.name,
                    _summary_line(result),
                )
                failures.append(f"{creator.name}: download errors (see the log)")

    if failures:
        notifier.send(
            f"PatronStash: {plural(len(failures), 'creator')} failed",
            "\n".join(failures),
        )
        return 1

    if cfg.notify_summary and new_posts:
        notifier.send(
            "PatronStash: run finished",
            f"Downloaded {plural(new_posts, 'new post')} "
            f"({plural(new_files, 'file')}).",
        )
    return 0
