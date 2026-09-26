import io
import logging
from datetime import datetime

from patronstash.logs import setup_console
from patronstash.progress import PostReporter, ProgressBar


def post(title, day=1):
    return {"title": title, "date": datetime(2026, 9, day)}


# ── PostReporter ───────────────────────────────────────────────────


def test_one_line_per_post_with_new_files(caplog):
    r = PostReporter("artist")
    with caplog.at_level(logging.INFO, logger="patronstash"):
        r.post_started(post("Stream night", 26))
        r.file_done()
        r.file_done()
        r.post_started(post("Nothing new here", 25))  # all files already archived
        r.post_started(post("Photo set", 24))
        r.file_done()
        r.finish_post()
    assert caplog.messages == [
        "artist: 2026-09-26 Stream night — 2 files",
        "artist: 2026-09-24 Photo set — 1 file",
    ]


def test_untitled_post(caplog):
    r = PostReporter("artist")
    with caplog.at_level(logging.INFO, logger="patronstash"):
        r.post_started({"title": "  ", "date": None})
        r.file_done()
        r.finish_post()
    assert caplog.messages == ["artist: Untitled — 1 file"]


def test_finish_post_twice_logs_once(caplog):
    r = PostReporter("artist")
    with caplog.at_level(logging.INFO, logger="patronstash"):
        r.post_started(post("A"))
        r.file_done()
        r.finish_post()
        r.finish_post()
    assert len(caplog.messages) == 1


def test_heartbeat_during_long_backfill(caplog):
    r = PostReporter("artist", heartbeat_every=3)
    with caplog.at_level(logging.INFO, logger="patronstash"):
        for day in range(1, 8):
            r.post_started(post("old", day))
    assert caplog.messages == [
        "artist: 3 posts checked, 0 new files so far…",
        "artist: 6 posts checked, 0 new files so far…",
    ]


# ── ProgressBar ────────────────────────────────────────────────────


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_bar_draws_on_a_terminal():
    out = io.StringIO()
    bar = ProgressBar(out, enabled=True, clock=Clock())
    bar.start("/dl/artist/post/youtube-AbCdEfGhIjK.mkv")
    bar.progress(541_000_000, 331_000_000, 4_200_000)
    text = out.getvalue()
    assert text.startswith("\r\033[K")
    assert "youtube-AbCdEfGhIjK.mkv" in text
    assert "[######----]" in text and " 61%" in text
    assert "331.0 MB / 541.0 MB" in text and "4.2 MB/s" in text


def test_bar_without_known_size():
    out = io.StringIO()
    bar = ProgressBar(out, enabled=True, clock=Clock())
    bar.start("/x/video.mp4")
    bar.progress(None, 5_000_000, 1_000_000)
    assert "video.mp4  5.0 MB  1.0 MB/s" in out.getvalue()


def test_bar_redraws_at_most_four_times_a_second():
    out, clock = io.StringIO(), Clock()
    bar = ProgressBar(out, enabled=True, clock=clock)
    bar.start("/x/a.mp4")
    bar.progress(100, 10, 1)
    clock.now = 0.1
    bar.progress(100, 20, 1)
    clock.now = 0.3
    bar.progress(100, 30, 1)
    assert out.getvalue().count("\r\033[K") == 2


def test_bar_is_cleared_when_the_file_finishes():
    out = io.StringIO()
    bar = ProgressBar(out, enabled=True, clock=Clock())
    bar.start("/x/a.mp4")
    bar.progress(100, 50, 1)
    bar.success("/x/a.mp4")
    assert out.getvalue().endswith("\r\033[K")
    assert bar.shown is False


def test_no_bar_without_a_terminal():
    out = io.StringIO()  # isatty() is False, like cron or systemd
    bar = ProgressBar(out)
    bar.start("/x/a.mp4")
    bar.progress(100, 50, 1)
    assert out.getvalue() == ""


def test_log_lines_clear_the_bar_first(capsys):
    import sys

    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    setup_console(verbose=False)

    bar = ProgressBar(sys.stderr, enabled=True, clock=Clock())
    with bar:
        bar.start("/x/a.mp4")
        bar.progress(100, 50, 1)
        logging.getLogger("patronstash").info("artist: Photo set — 1 file")
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    err = capsys.readouterr().err
    # the bar, then an erase, then the log line on a clean line
    assert err.index("50%") < err.rindex("\r\033[K") < err.index("artist: Photo set")
