"""stats.db: one row per downloaded file, plus per-creator run state.

gallery-dl's own archive stores only IDs. This database adds sizes, dates
and totals for `patronstash status`, and tracks each creator's backfill.
All timestamps are naive UTC, stored as ISO 8601 text.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Backfill

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    creator       TEXT NOT NULL,
    post_id       TEXT NOT NULL,
    post_date     TEXT,
    path          TEXT NOT NULL,
    size          INTEGER NOT NULL,
    downloaded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_creator ON files (creator);

CREATE TABLE IF NOT EXISTS creators (
    name           TEXT PRIMARY KEY,
    backfill       TEXT NOT NULL,     -- the config setting this state is for
    complete       INTEGER NOT NULL,  -- 1 once a clean pass reached the cutoff
    cutoff         TEXT,              -- oldest post date to fetch; NULL = all
    last_run       TEXT,
    last_result    TEXT
);
"""


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


@dataclass
class CreatorState:
    name: str
    backfill: str
    complete: bool
    cutoff: datetime | None
    last_run: datetime | None = None
    last_result: str | None = None


@dataclass
class FileSummary:
    posts: int
    files: int
    bytes: int
    last_post_date: datetime | None


class StatsDB:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.conn.close()

    # ── creators ────────────────────────────────────────────────────

    def get_creator(self, name: str) -> CreatorState | None:
        row = self.conn.execute(
            "SELECT * FROM creators WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return CreatorState(
            name=row["name"],
            backfill=row["backfill"],
            complete=bool(row["complete"]),
            cutoff=_dt(row["cutoff"]),
            last_run=_dt(row["last_run"]),
            last_result=row["last_result"],
        )

    def begin_creator(
        self, name: str, backfill: Backfill, now: datetime
    ) -> CreatorState:
        """Return the creator's state, (re)starting it if the setting changed."""
        state = self.get_creator(name)
        if state is not None and state.backfill == str(backfill):
            return state

        if backfill.mode == "none":
            complete, cutoff = True, now
        elif backfill.mode == "since":
            complete = False
            cutoff = datetime(
                backfill.since.year, backfill.since.month, backfill.since.day
            )
        else:
            complete, cutoff = False, None

        with self.conn:
            self.conn.execute(
                "INSERT INTO creators (name, backfill, complete, cutoff) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (name) DO UPDATE SET "
                "backfill = excluded.backfill, complete = excluded.complete, "
                "cutoff = excluded.cutoff",
                (name, str(backfill), int(complete), _iso(cutoff)),
            )
        return self.get_creator(name)

    def finish_creator(
        self,
        name: str,
        result: str,
        now: datetime,
        *,
        backfill_complete: bool,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE creators SET last_run = ?, last_result = ?, "
                "complete = MAX(complete, ?) WHERE name = ?",
                (_iso(now), result, int(backfill_complete), name),
            )

    # ── files ───────────────────────────────────────────────────────

    def record_file(
        self,
        creator: str,
        post_id: str,
        post_date: datetime | None,
        path: str,
        size: int,
        downloaded_at: datetime,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO files (creator, post_id, post_date, path, size, "
                "downloaded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    creator,
                    str(post_id),
                    _iso(post_date),
                    path,
                    size,
                    _iso(downloaded_at),
                ),
            )

    def file_summary(self, creator: str) -> FileSummary:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT post_id), COUNT(*), COALESCE(SUM(size), 0), "
            "MAX(post_date) FROM files WHERE creator = ?",
            (creator,),
        ).fetchone()
        return FileSummary(row[0], row[1], row[2], _dt(row[3]))
