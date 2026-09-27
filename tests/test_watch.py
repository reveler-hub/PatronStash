import logging
from datetime import datetime

import pytest

from patronstash import gdl
from patronstash.gdl import CreatorRun, DownloadedFile
from patronstash.stats import StatsDB
from patronstash.watch import Watcher

CONFIG = """
download_dir = "{dl}"
cookies_file = "/c.txt"

[[creator]]
name = "alpha"
backfill = "all"

[[creator]]
name = "beta"
backfill = "none"

[[creator]]
name = "forgot"
"""

NOW = datetime(2026, 9, 27, 12, 0)


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG.replace("{dl}", str(tmp_path / "dl")))
    return path


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def fake_run_creator(outcomes):
    """Stands in for gdl.run_creator: reports files and progress like it."""

    def run_creator(cfg, creator, *, on_file, progress, **kwargs):
        result = CreatorRun()
        for i in range(outcomes.get(creator.name, 0)):
            with progress:
                progress.start(f"/dl/{creator.name}/post/{i:02d}.jpg")
                progress.progress(100, 50, 10)
                progress.success(f"/dl/{creator.name}/post/{i:02d}.jpg")
            f = DownloadedFile(str(i), NOW, f"/x/{i}", 1000)
            on_file(f)
            result.files.append(f)
        if creator.name in outcomes.get("fail", ()):
            result.status = 4
        return result

    return run_creator


def fake_runner(watcher_calls):
    """Stands in for runner.run: calls the downloader for each creator."""

    def run(cfg, *, notifier, downloader, **kwargs):
        watcher_calls.append(cfg)
        for creator in cfg.creators:
            downloader(
                cfg,
                creator,
                cutoff=None,
                complete=False,
                allow_video=True,
                verbose=False,
                on_file=lambda f: None,
            )
        return 0

    return run


def make_watcher(config_path, calls=None, clock=None, **kwargs):
    calls = [] if calls is None else calls
    return Watcher(
        config_path,
        every=3600,
        clock=clock or FakeClock(),
        run=fake_runner(calls),
        **kwargs,
    )


def rows(watcher):
    return {r.name: r for r in watcher.state().rows}


def test_rows_come_from_config_and_stats(config_path, tmp_path):
    w = make_watcher(config_path)
    w.reload()
    cfg = w.cfg
    with StatsDB(cfg.data_dir / "stats.db") as db:
        db.record_file("alpha", "1", NOW, "/a", 2_000_000, NOW)
        db.record_file("alpha", "2", datetime(2026, 9, 1), "/b", 1_000_000, NOW)
    w.refresh_rows()
    r = rows(w)
    assert list(r) == ["alpha", "beta", "forgot"]
    assert (r["alpha"].posts, r["alpha"].size, r["alpha"].newest) == (
        2,
        3_000_000,
        "2026-09-27",
    )
    assert r["beta"].posts == 0 and r["beta"].activity == "nothing archived yet"
    assert r["forgot"].kind == "error" and "backfill" in r["forgot"].activity


def test_a_pass_updates_every_creator(config_path, monkeypatch):
    monkeypatch.setattr(gdl, "run_creator", fake_run_creator({"alpha": 3}))
    calls = []
    w = make_watcher(config_path, calls)
    w.run_pass()
    r = rows(w)
    assert len(calls) == 1
    assert r["alpha"].kind == "ok" and r["alpha"].status == "✓ 3 new"
    assert "3 files" in r["alpha"].activity
    assert r["beta"].status == "✓ Up to date"
    state = w.state()
    assert state.pass_progress is None and state.active is None


def test_live_progress_is_shown_while_downloading(config_path, monkeypatch):
    seen = []

    def run_creator(cfg, creator, *, on_file, progress, **kwargs):
        with progress:
            progress.start("/dl/alpha/post/youtube-AbCdEfGhIjK.mkv")
            progress.progress(541_000_000, 331_000_000, 4_200_000)
            seen.append(w.state())
        return CreatorRun()

    monkeypatch.setattr(gdl, "run_creator", run_creator)
    w = make_watcher(config_path)
    w.run_pass()
    during = seen[0]
    assert during.active.creator == "alpha"
    assert during.active.filename == "youtube-AbCdEfGhIjK.mkv"
    assert (during.active.done, during.active.total) == (331_000_000, 541_000_000)
    assert during.pass_progress == (0, 2)
    assert w.state().active is None  # cleared afterwards


def test_failed_creator_shows_an_error(config_path, monkeypatch):
    monkeypatch.setattr(gdl, "run_creator", fake_run_creator({"fail": ["beta"]}))
    w = make_watcher(config_path)
    w.run_pass()
    assert rows(w)["beta"].kind == "error"


def test_passes_are_scheduled(config_path, monkeypatch):
    monkeypatch.setattr(gdl, "run_creator", fake_run_creator({}))
    clock, calls = FakeClock(), []
    w = make_watcher(config_path, calls, clock)
    w.tick()  # the first pass runs straight away
    assert len(calls) == 1
    assert w.state().next_pass_in == 3600
    clock.now += 1800
    w.tick()
    assert len(calls) == 1 and w.state().next_pass_in == 1800
    w.on_key(ord("r"))  # run now
    w.tick()
    assert len(calls) == 2
    clock.now += 3600
    w.tick()
    assert len(calls) == 3


def test_config_is_reread_before_each_pass(config_path, monkeypatch):
    monkeypatch.setattr(gdl, "run_creator", fake_run_creator({}))
    w = make_watcher(config_path)
    w.run_pass()
    config_path.write_text(
        config_path.read_text() + '\n[[creator]]\nname = "gamma"\nbackfill = "all"\n'
    )
    w.run_pass()
    assert "gamma" in rows(w)


def test_broken_config_keeps_watching(config_path, monkeypatch):
    monkeypatch.setattr(gdl, "run_creator", fake_run_creator({}))
    calls = []
    w = make_watcher(config_path, calls)
    w.run_pass()
    config_path.write_text("download_dir = = broken")
    w.run_pass()
    assert len(calls) == 1  # the broken config didn't run
    assert "TOML" in w.state().notice


def test_engine_messages_reach_the_dashboard(config_path):
    w = make_watcher(config_path)
    handler = w.log_handler()
    log = logging.getLogger("patronstash")
    log.addHandler(handler)
    level = log.level
    log.setLevel(logging.INFO)
    try:
        log.error("login failed: the Patreon session has expired")
        assert "login failed" in w.state().notice
        log.info(
            "PatronStash 0.1.9 is available (you have 0.1.3). Update with:\n  pipx …"
        )
        assert "0.1.9 is available" in w.state().notice
        assert "\n" not in w.state().notice
        assert w.state().notice.endswith("(you have 0.1.3). See the README to update.")
    finally:
        log.removeHandler(handler)
        log.setLevel(level)


def test_counts_go_up_as_files_finish(config_path, monkeypatch):
    seen = []

    def run_creator(cfg, creator, *, on_file, progress, **kwargs):
        if creator.name == "alpha":
            on_file(DownloadedFile("7", NOW, "/x/1", 500))
            on_file(DownloadedFile("7", NOW, "/x/2", 300))
            seen.append({r.name: r for r in w.state().rows}["alpha"])
        return CreatorRun()

    monkeypatch.setattr(gdl, "run_creator", run_creator)
    w = make_watcher(config_path)
    w.run_pass()
    during = seen[0]
    assert (during.posts, during.size, during.newest) == (1, 800, "2026-09-27")
