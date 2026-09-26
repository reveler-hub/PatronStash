"""The `patronstash` command."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, default_config_path, load_config, write_template

log = logging.getLogger("patronstash")

DESCRIPTION = (
    "Archive posts, images, videos and attachments from the Patreon "
    "creators you support. Unofficial; not affiliated with Patreon."
)


def _global_options(parser: argparse.ArgumentParser, suppress: bool) -> None:
    # Accepted before or after the subcommand; SUPPRESS keeps the
    # subcommand's defaults from overwriting values given before it.
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        default=default,
        help=f"config file (default: {default_config_path()})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS if suppress else False,
        help="show gallery-dl's full per-file output",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patronstash", description=DESCRIPTION)
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    _global_options(parser, suppress=False)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, text in (
        ("run", "download everything new for every creator, then exit"),
        ("check", "validate the setup without downloading anything"),
        ("status", "show one line per creator"),
    ):
        _global_options(
            commands.add_parser(name, help=text, description=text), suppress=True
        )
    return parser


def _create_template(path: Path) -> None:
    write_template(path)
    print(
        f"Created a new config file at {path}\n"
        "Edit it (set download_dir, a login and your creators), then run "
        "`patronstash check`.",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    from .logs import add_log_file, setup_console

    setup_console(args.verbose)

    path = args.config or default_config_path()
    if args.config is None and not path.exists():
        _create_template(path)  # first use: nothing to run yet
        return 2

    if args.command == "check":
        from .check import run_check

        return run_check(path)

    try:
        cfg = load_config(path)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    if args.command == "status":
        from .stats import StatsDB
        from .status import format_status

        with StatsDB(cfg.data_dir / "stats.db") as stats:
            print(format_status(cfg, stats))
        return 0

    from .notify import Notifier
    from .runner import run

    add_log_file(cfg.data_dir)
    log.info("run started (PatronStash %s)", __version__, extra={"console": False})
    notifier = Notifier(cfg.notify_url)
    try:
        return run(cfg, notifier=notifier, verbose=args.verbose)
    except KeyboardInterrupt:
        log.warning("interrupted; the next run resumes where this one stopped")
        return 130
    except Exception as exc:
        log.exception("run failed")
        notifier.send("PatronStash: run failed", f"{exc.__class__.__name__}: {exc}")
        return 1
