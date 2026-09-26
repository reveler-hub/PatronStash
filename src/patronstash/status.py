"""`patronstash status`: one line per creator."""

from __future__ import annotations

from .config import Config, Creator
from .fmt import human_size, local_time
from .stats import CreatorState, StatsDB

HEADERS = ("Creator", "Backfill", "Posts", "Files", "Size", "Last post", "Last run")


def _backfill_text(creator: Creator, state: CreatorState | None) -> str:
    backfill = creator.backfill
    if state is not None and state.backfill != str(backfill):
        return f"{_setting(creator)}, restarts next run"
    if backfill.mode == "none":
        if state is None:
            return "new posts only, not started"
        return f"new posts only (from {state.cutoff:%Y-%m-%d})"
    if state is None:
        return f"{_setting(creator)}, not started"
    return f"{_setting(creator)}, {'done' if state.complete else 'in progress'}"


def _setting(creator: Creator) -> str:
    backfill = creator.backfill
    if backfill.mode == "since":
        return f"since {backfill}"
    return "new posts only" if backfill.mode == "none" else "all"


def format_status(cfg: Config, stats: StatsDB) -> str:
    if not cfg.creators and not cfg.missing_backfill:
        return f"No creators configured yet. Add [[creator]] blocks to {cfg.path}"

    rows = []
    for creator in cfg.creators:
        state = stats.get_creator(creator.name)
        summary = stats.file_summary(creator.name)
        if state is not None and state.last_run is not None:
            last_run = f"{local_time(state.last_run)} {state.last_result}"
        else:
            last_run = "never"
        rows.append(
            (
                creator.name,
                _backfill_text(creator, state),
                str(summary.posts),
                str(summary.files),
                human_size(summary.bytes),
                f"{summary.last_post_date:%Y-%m-%d}" if summary.last_post_date else "-",
                last_run,
            )
        )
    for name in cfg.missing_backfill:
        rows.append((name, "no backfill line: skipped", "", "", "", "", ""))

    widths = [max(len(r[i]) for r in [HEADERS, *rows]) for i in range(len(HEADERS))]
    lines = [
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in [HEADERS, *rows]
    ]
    return "\n".join(lines)
