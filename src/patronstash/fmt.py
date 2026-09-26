"""Small formatting helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    raise AssertionError("unreachable")


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def utcnow() -> datetime:
    """The current time as naive UTC, matching gallery-dl's post dates."""
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def local_time(value: datetime) -> str:
    """Show a naive-UTC timestamp in the local time zone."""
    return value.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M")
