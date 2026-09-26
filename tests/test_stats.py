from datetime import date, datetime

import pytest

from patronstash.config import Backfill
from patronstash.stats import StatsDB

NOW = datetime(2026, 9, 26, 12, 0, 0)
LATER = datetime(2026, 9, 27, 12, 0, 0)


@pytest.fixture
def db(tmp_path):
    with StatsDB(tmp_path / "stats.db") as db:
        yield db


def test_new_all_creator_starts_in_progress(db):
    state = db.begin_creator("a", Backfill("all"), NOW)
    assert state.backfill == "all"
    assert state.complete is False
    assert state.cutoff is None


def test_new_none_creator_is_complete_with_cutoff_now(db):
    state = db.begin_creator("a", Backfill("none"), NOW)
    assert state.complete is True
    assert state.cutoff == NOW


def test_since_creator_cutoff_is_the_date(db):
    state = db.begin_creator("a", Backfill("since", date(2024, 1, 1)), NOW)
    assert state.complete is False
    assert state.cutoff == datetime(2024, 1, 1)


def test_none_cutoff_is_kept_across_runs(db):
    db.begin_creator("a", Backfill("none"), NOW)
    db.finish_creator("a", "ok", NOW, backfill_complete=True)
    state = db.begin_creator("a", Backfill("none"), LATER)
    assert state.cutoff == NOW


def test_backfill_completes_after_clean_run(db):
    db.begin_creator("a", Backfill("all"), NOW)
    db.finish_creator("a", "ok", NOW, backfill_complete=True)
    assert db.begin_creator("a", Backfill("all"), LATER).complete is True


def test_backfill_stays_in_progress_after_failed_run(db):
    db.begin_creator("a", Backfill("all"), NOW)
    db.finish_creator("a", "error", NOW, backfill_complete=False)
    state = db.get_creator("a")
    assert state.complete is False
    assert state.last_result == "error"
    assert state.last_run == NOW


def test_changing_backfill_resets_state(db):
    db.begin_creator("a", Backfill("none"), NOW)
    db.finish_creator("a", "ok", NOW, backfill_complete=True)
    state = db.begin_creator("a", Backfill("all"), LATER)
    assert state.backfill == "all"
    assert state.complete is False
    assert state.cutoff is None


def test_get_unknown_creator(db):
    assert db.get_creator("nobody") is None


def test_record_files_and_summary(db):
    db.record_file("a", "1", datetime(2024, 1, 1), "/x/1.jpg", 100, NOW)
    db.record_file("a", "1", datetime(2024, 1, 1), "/x/2.jpg", 50, NOW)
    db.record_file("a", "2", datetime(2024, 3, 1), "/x/3.jpg", 10, NOW)
    db.record_file("b", "9", datetime(2023, 1, 1), "/y/1.jpg", 7, NOW)

    s = db.file_summary("a")
    assert s.posts == 2
    assert s.files == 3
    assert s.bytes == 160
    assert s.last_post_date == datetime(2024, 3, 1)


def test_summary_for_creator_without_files(db):
    s = db.file_summary("nobody")
    assert (s.posts, s.files, s.bytes, s.last_post_date) == (0, 0, 0, None)


def test_state_survives_reopen(tmp_path):
    path = tmp_path / "stats.db"
    with StatsDB(path) as db:
        db.begin_creator("a", Backfill("all"), NOW)
        db.finish_creator("a", "ok", NOW, backfill_complete=True)
        db.record_file("a", "1", datetime(2024, 1, 1), "/x", 1, NOW)
    with StatsDB(path) as db:
        assert db.get_creator("a").complete is True
        assert db.file_summary("a").files == 1
