"""The `run` command, with gallery-dl, login and notifications faked."""

import logging
from datetime import date, datetime
from pathlib import Path

from patronstash import runner
from patronstash.config import Backfill, Config, Creator, Login
from patronstash.gdl import CreatorRun, DownloadedFile, LoginResult, StreamCounts
from patronstash.lock import run_lock
from patronstash.stats import StatsDB

NOW = datetime(2026, 9, 26, 12, 0)


class FakeNotifier:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.sent = []

    def send(self, title, body):
        self.sent.append((title, body))
        return True


class FakeDownloader:
    """Stands in for gdl.run_creator."""

    def __init__(self, outcomes=None):
        self.outcomes = outcomes or {}
        self.calls = []

    def __call__(
        self, cfg, creator, *, cutoff, complete, allow_video, verbose, on_file
    ):
        self.calls.append(
            {
                "name": creator.name,
                "cutoff": cutoff,
                "complete": complete,
                "allow_video": allow_video,
            }
        )
        outcome = self.outcomes.get(creator.name, {})
        if isinstance(outcome, Exception):
            raise outcome
        result = CreatorRun(status=outcome.get("status", 0))
        for i, post_id in enumerate(outcome.get("posts", [])):
            f = DownloadedFile(str(post_id), datetime(2024, 1, 1), f"/x/{i}", 10)
            on_file(f)
            result.files.append(f)
        result.counts = StreamCounts(
            locked=outcome.get("locked", 0),
            videos_skipped=outcome.get("videos_skipped", 0),
        )
        return result


def make_config(tmp_path, creators, missing=(), summary=False):
    return Config(
        path=tmp_path / "config.toml",
        download_dir=tmp_path / "dl",
        data_dir=tmp_path / "data",
        login=Login("cookies_file", cookies_file=Path("/c.txt")),
        notify_url="ntfy://x",
        notify_summary=summary,
        creators=[Creator(n, Backfill.parse(b)) for n, b in creators],
        missing_backfill=list(missing),
    )


def do_run(cfg, downloader=None, login_ok=True, ffmpeg=True, notifier=None, tags=None):
    notifier = notifier or FakeNotifier()
    downloader = downloader or FakeDownloader()
    code = runner.run(
        cfg,
        notifier=notifier,
        verbose=False,
        now=lambda: NOW,
        login_checker=lambda cfg: LoginResult(
            login_ok, "logged in as Tester" if login_ok else "session expired"
        ),
        downloader=downloader,
        ffmpeg_available=lambda: ffmpeg,
        fetch_tags=(lambda: tags) if tags is not None else None,
    )
    return code, notifier, downloader


def state(cfg, name):
    with StatsDB(cfg.data_dir / "stats.db") as db:
        return db.get_creator(name)


def test_clean_run_sends_no_notification(tmp_path):
    cfg = make_config(tmp_path, [("a", "all"), ("b", "none")])
    code, notifier, dl = do_run(cfg, FakeDownloader({"a": {"posts": [1, 1, 2]}}))
    assert code == 0
    assert notifier.sent == []
    assert [c["name"] for c in dl.calls] == ["a", "b"]


def test_files_are_recorded_in_stats(tmp_path):
    cfg = make_config(tmp_path, [("a", "all")])
    do_run(cfg, FakeDownloader({"a": {"posts": [1, 1, 2]}}))
    with StatsDB(cfg.data_dir / "stats.db") as db:
        assert db.file_summary("a").files == 3
        assert db.file_summary("a").posts == 2


def test_backfill_progress(tmp_path):
    cfg = make_config(tmp_path, [("a", "all"), ("b", "2024-01-01"), ("c", "none")])
    _, _, dl = do_run(cfg)
    first = {c["name"]: c for c in dl.calls}
    assert first["a"]["complete"] is False and first["a"]["cutoff"] is None
    assert first["b"]["cutoff"] == datetime(2024, 1, 1)
    assert first["c"]["complete"] is True and first["c"]["cutoff"] == NOW

    _, _, dl = do_run(cfg)
    assert all(c["complete"] for c in dl.calls)


def test_failed_creator_stays_in_backfill_and_notifies(tmp_path):
    cfg = make_config(tmp_path, [("a", "all"), ("b", "all")])
    code, notifier, _ = do_run(cfg, FakeDownloader({"a": {"status": 4}}))
    assert code == 1
    assert state(cfg, "a").complete is False
    assert state(cfg, "a").last_result == "error"
    assert state(cfg, "b").complete is True
    assert len(notifier.sent) == 1
    assert "a" in notifier.sent[0][1]


def test_crashing_creator_does_not_stop_others(tmp_path):
    cfg = make_config(tmp_path, [("a", "all"), ("b", "all")])
    code, notifier, dl = do_run(cfg, FakeDownloader({"a": RuntimeError("boom")}))
    assert code == 1
    assert [c["name"] for c in dl.calls] == ["a", "b"]
    assert state(cfg, "b").last_result == "ok"
    assert "boom" in notifier.sent[0][1]


def test_expired_login_downloads_nothing(tmp_path):
    cfg = make_config(tmp_path, [("a", "all")])
    code, notifier, dl = do_run(cfg, login_ok=False)
    assert code == 1
    assert dl.calls == []
    assert len(notifier.sent) == 1
    assert "session expired" in notifier.sent[0][1]


def test_missing_backfill_notifies_but_others_run(tmp_path, caplog):
    cfg = make_config(tmp_path, [("a", "all")], missing=["forgot"])
    with caplog.at_level(logging.WARNING):
        code, notifier, dl = do_run(cfg)
    assert code == 0
    assert [c["name"] for c in dl.calls] == ["a"]
    assert len(notifier.sent) == 1
    assert "forgot" in notifier.sent[0][1]
    assert "forgot" in caplog.text


def test_summary_notification_when_enabled(tmp_path):
    cfg = make_config(tmp_path, [("a", "all"), ("b", "all")], summary=True)
    _, notifier, _ = do_run(
        cfg, FakeDownloader({"a": {"posts": [1, 2]}, "b": {"posts": [7]}})
    )
    assert len(notifier.sent) == 1
    assert "3 new posts" in notifier.sent[0][1]


def test_no_summary_when_nothing_new(tmp_path):
    cfg = make_config(tmp_path, [("a", "all")], summary=True)
    _, notifier, _ = do_run(cfg)
    assert notifier.sent == []


def test_no_summary_on_failure_only_the_error(tmp_path):
    cfg = make_config(tmp_path, [("a", "all"), ("b", "all")], summary=True)
    _, notifier, _ = do_run(
        cfg, FakeDownloader({"a": {"status": 4}, "b": {"posts": [1]}})
    )
    assert len(notifier.sent) == 1
    assert "failed" in notifier.sent[0][0].lower()


def test_without_ffmpeg_video_is_skipped_and_backfill_not_completed(tmp_path, caplog):
    cfg = make_config(tmp_path, [("a", "all")])
    with caplog.at_level(logging.WARNING):
        _, _, dl = do_run(
            cfg, FakeDownloader({"a": {"videos_skipped": 2}}), ffmpeg=False
        )
    assert dl.calls[0]["allow_video"] is False
    assert caplog.text.count("ffmpeg") == 1
    assert state(cfg, "a").complete is False


def test_already_running(tmp_path, caplog):
    cfg = make_config(tmp_path, [("a", "all")])
    with run_lock(cfg.data_dir / "patronstash.lock"):
        with caplog.at_level(logging.INFO):
            code, notifier, dl = do_run(cfg)
    assert code == 0
    assert dl.calls == []
    assert "already running" in caplog.text


def test_changed_backfill_restarts(tmp_path):
    cfg = make_config(tmp_path, [("a", "none")])
    do_run(cfg)
    cfg.creators = [Creator("a", Backfill("since", date(2020, 1, 1)))]
    _, _, dl = do_run(cfg)
    assert dl.calls[0]["cutoff"] == datetime(2020, 1, 1)
    assert dl.calls[0]["complete"] is False


def test_locked_posts_are_in_the_summary_line(tmp_path, caplog):
    cfg = make_config(tmp_path, [("a", "all")])
    with caplog.at_level(logging.INFO, logger="patronstash"):
        do_run(cfg, FakeDownloader({"a": {"locked": 3}}))
    assert "a: nothing new; 3 locked posts skipped" in caplog.messages


def test_each_creator_announces_itself(tmp_path, caplog):
    cfg = make_config(tmp_path, [("a", "all"), ("b", "none")])
    with caplog.at_level(logging.INFO, logger="patronstash"):
        do_run(cfg)
    assert "a: checking for new posts…" in caplog.messages
    assert "b: checking for new posts…" in caplog.messages


# ── update check ───────────────────────────────────────────────────


def test_new_version_is_announced_and_notified_once(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr("patronstash.runner.__version__", "0.1.3")
    cfg = make_config(tmp_path, [("a", "none")])
    with caplog.at_level(logging.INFO, logger="patronstash"):
        _, notifier, _ = do_run(cfg, tags=["v0.1.4", "v0.1.3"])
    assert any(
        "PatronStash 0.1.4 is available (you have 0.1.3)" in m for m in caplog.messages
    )
    assert notifier.sent == [
        (
            "PatronStash update available",
            "PatronStash 0.1.4 is available (you have 0.1.3).",
        )
    ]
    _, notifier, _ = do_run(
        cfg, tags=["v0.1.4"]
    )  # same version: no second notification
    assert notifier.sent == []


def test_up_to_date_says_nothing(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr("patronstash.runner.__version__", "0.1.4")
    cfg = make_config(tmp_path, [("a", "none")])
    with caplog.at_level(logging.INFO, logger="patronstash"):
        _, notifier, _ = do_run(cfg, tags=["v0.1.4"])
    assert not any("available" in m for m in caplog.messages)
    assert notifier.sent == []


def test_update_check_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr("patronstash.runner.__version__", "0.1.3")
    cfg = make_config(tmp_path, [("a", "none")])
    cfg.update_check = False

    def must_not_fetch():
        raise AssertionError("contacted GitHub")

    code = runner.run(
        cfg,
        notifier=FakeNotifier(),
        now=lambda: NOW,
        login_checker=lambda cfg: LoginResult(True, "ok"),
        downloader=FakeDownloader(),
        ffmpeg_available=lambda: True,
        fetch_tags=must_not_fetch,
    )
    assert code == 0


def test_unreachable_github_never_fails_a_run(tmp_path, monkeypatch):
    monkeypatch.setattr("patronstash.runner.__version__", "0.1.3")
    cfg = make_config(tmp_path, [("a", "none")])
    code, notifier, _ = do_run(cfg)  # conftest makes GitHub unreachable
    assert code == 0 and notifier.sent == []
