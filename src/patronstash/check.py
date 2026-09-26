"""`patronstash check`: validate the setup without downloading anything."""

from __future__ import annotations

import os
from pathlib import Path

from . import gdl
from .config import ConfigError, load_config
from .fmt import plural
from .notify import Notifier
from .runner import ffmpeg_available as default_ffmpeg_available

OK, WARN, FAIL = "✅", "⚠", "❌"


def run_check(
    path: Path,
    *,
    login_checker=gdl.check_login,
    ffmpeg_available=default_ffmpeg_available,
    deno_available=gdl.deno_available,
    make_notifier=Notifier,
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
        if not any(key in warning for key in cfg.reserved_keys):
            report(WARN, f"Config: {warning}")

    for key in cfg.reserved_keys:
        report(WARN, f"{key} is reserved by PatronStash and will be ignored; remove it")

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

    login = login_checker(cfg)
    report(OK if login.ok else FAIL, f"Login: {login.message}")

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

    notifier = make_notifier(cfg.notify_url)
    if notifier.enabled:
        if notifier.send("PatronStash test", "Notifications from PatronStash work."):
            report(OK, "Notifications: test notification sent")
        else:
            report(FAIL, "Notifications: the test notification could not be sent")
    else:
        report(OK, "Notifications: not configured (optional)")

    return 1 if failed else 0
