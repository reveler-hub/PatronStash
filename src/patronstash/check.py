"""`patronstash check`: validate the setup without downloading anything."""

from __future__ import annotations

import os
from pathlib import Path

from . import __version__, gdl, updates
from .config import ConfigError, load_config
from .fmt import plural, utcnow
from .notify import Notifier

OK, WARN, FAIL = "✅", "⚠", "❌"


def run_check(
    path: Path,
    *,
    login_checker=gdl.check_login,
    ffmpeg_available=gdl.ffmpeg_available,
    deno_available=gdl.deno_available,
    make_notifier=Notifier,
    fetch_tags=None,
    now=utcnow,
    version=__version__,
    out=print,
) -> int:
    failed = False

    def report(mark: str, text: str):
        nonlocal failed
        failed = failed or mark == FAIL
        out(f"{mark} {text}")

    try:
        cfg = load_config(path)
    except ConfigError as exc:
        report(FAIL, f"Config: {exc}")
        return 1
    report(OK, f"Config parses: {cfg.path}")

    for warning in cfg.warnings:
        report(WARN, f"Config: {warning}")

    if cfg.creators:
        report(OK, f"{plural(len(cfg.creators), 'creator')} with a backfill line")
    elif not cfg.missing_backfill:
        report(WARN, "No creators configured yet; add [[creator]] blocks")
    for name in cfg.missing_backfill:
        report(WARN, f"Creator '{name}' has no backfill line and will be skipped")

    if cfg.download_dir.is_dir():
        if os.access(cfg.download_dir, os.W_OK):
            report(OK, f"download_dir: {cfg.download_dir}")
        else:
            report(FAIL, f"download_dir is not writable: {cfg.download_dir}")
    else:
        report(
            WARN,
            f"download_dir does not exist yet and will be created: {cfg.download_dir}",
        )

    try:
        login = login_checker(cfg)
    finally:
        gdl.close_api_extractors()
    report(OK if login.ok else FAIL, f"Login: {login.message}")

    if cfg.youtube_cookies_file is not None:
        if cfg.youtube_cookies_file.is_file():
            report(OK, f"YouTube cookies: {cfg.youtube_cookies_file}")
        else:
            report(FAIL, f"YouTube cookies file not found: {cfg.youtube_cookies_file}")

    if ffmpeg_available():
        report(OK, "ffmpeg is installed")
    else:
        report(WARN, "ffmpeg not found: video will be skipped until it is installed")

    if deno_available():
        report(OK, "deno is installed: YouTube videos download in full quality")
    else:
        report(
            WARN,
            "deno not found: YouTube videos may be limited to lower quality",
        )

    if not cfg.update_check:
        report(OK, "Update check: turned off (update_check = false)")
    else:
        info = updates.check_for_update(
            cfg.data_dir, version, now=now(), force=True, fetch=fetch_tags
        )
        if info is None:
            report(WARN, "Update check: couldn't reach GitHub to check for updates")
        elif info.available:
            report(WARN, info.message())
        else:
            report(OK, f"PatronStash {version} is the latest version")

    notifier = make_notifier(cfg.notify_url)
    if notifier.enabled:
        if notifier.send("PatronStash test", "Notifications from PatronStash work."):
            report(OK, "Notifications: test notification sent")
        else:
            report(FAIL, "Notifications: the test notification could not be sent")
    else:
        report(OK, "Notifications: not configured (optional)")

    return 1 if failed else 0
