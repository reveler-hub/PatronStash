from datetime import UTC, datetime
from pathlib import Path

from patronstash.config import Backfill, Config, Creator, Login
from patronstash.fmt import human_size
from patronstash.stats import StatsDB
from patronstash.status import format_status

NOW = datetime(2026, 9, 26, 12, 0)


def make_config(tmp_path, creators, missing=()):
    return Config(
        path=tmp_path / "config.toml",
        download_dir=tmp_path / "dl",
        data_dir=tmp_path / "data",
        login=Login("cookies_file", cookies_file=Path("/c.txt")),
        notify_url=None,
        notify_summary=False,
        creators=[Creator(n, Backfill.parse(b)) for n, b in creators],
        missing_backfill=list(missing),
    )


def lines_by_creator(text):
    return {line.split()[0]: line for line in text.splitlines()[1:]}


def local(dt):
    return dt.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M")


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(999) == "999 B"
    assert human_size(1500) == "1.5 KB"
    assert human_size(12_300_000_000) == "12.3 GB"


def test_status_table(tmp_path):
    cfg = make_config(
        tmp_path,
        [
            ("done", "all"),
            ("partial", "2024-01-01"),
            ("newonly", "none"),
            ("fresh", "all"),
        ],
        missing=["forgot"],
    )
    with StatsDB(cfg.data_dir / "stats.db") as db:
        db.begin_creator("done", Backfill("all"), NOW)
        db.record_file("done", "1", datetime(2026, 9, 20), "/a", 2_000_000, NOW)
        db.record_file("done", "1", datetime(2026, 9, 20), "/b", 1_000_000, NOW)
        db.record_file("done", "2", datetime(2026, 9, 1), "/c", 500_000, NOW)
        db.finish_creator("done", "ok", NOW, backfill_complete=True)

        db.begin_creator("partial", Backfill.parse("2024-01-01"), NOW)
        db.finish_creator("partial", "error", NOW, backfill_complete=False)

        db.begin_creator("newonly", Backfill("none"), NOW)
        db.finish_creator("newonly", "ok", NOW, backfill_complete=True)

        text = format_status(cfg, db)

    rows = lines_by_creator(text)
    assert text.splitlines()[0].split()[:2] == ["Creator", "Backfill"]

    assert "all, done" in rows["done"]
    assert "2 " in rows["done"] and "3 " in rows["done"]
    assert "3.5 MB" in rows["done"]
    assert "2026-09-20" in rows["done"]
    assert f"{local(NOW)} ok" in rows["done"]

    assert "since 2024-01-01, in progress" in rows["partial"]
    assert "error" in rows["partial"]

    assert "new posts only (from 2026-09-26)" in rows["newonly"]

    assert "not started" in rows["fresh"]
    assert "never" in rows["fresh"]

    assert "no backfill line" in rows["forgot"]


def test_status_after_backfill_change(tmp_path):
    cfg = make_config(tmp_path, [("a", "all")])
    with StatsDB(cfg.data_dir / "stats.db") as db:
        db.begin_creator("a", Backfill("none"), NOW)
        db.finish_creator("a", "ok", NOW, backfill_complete=True)
        text = format_status(cfg, db)
    assert "restarts next run" in lines_by_creator(text)["a"]


def test_status_without_creators(tmp_path):
    cfg = make_config(tmp_path, [])
    with StatsDB(cfg.data_dir / "stats.db") as db:
        assert "no creators" in format_status(cfg, db).lower()
