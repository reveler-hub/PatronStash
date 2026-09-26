"""gallery-dl integration, with Patreon replaced by a fake extractor."""

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from gallery_dl.extractor.common import Extractor
from gallery_dl.extractor.message import Message

from patronstash.config import Config, Login
from patronstash.gdl import (
    SKIP_ABORT_AFTER,
    VIDEO_FORMAT,
    ArchiveJob,
    PostStream,
    apply_gdl_config,
    build_gdl_config,
    lookup_campaign_id,
    posts_are_newest_first,
)


def make_config(tmp_path, passthrough=None, login=None):
    return Config(
        path=tmp_path / "config.toml",
        download_dir=tmp_path / "dl",
        data_dir=tmp_path / "data",
        login=login or Login("cookies_file", cookies_file=Path("/c/cookies.txt")),
        notify_url=None,
        notify_summary=False,
        creators=[],
        passthrough=passthrough or {},
    )


# ── build_gdl_config ────────────────────────────────────────────────


def test_defaults(tmp_path):
    s = build_gdl_config(make_config(tmp_path))
    ex = s["extractor"]
    assert ex["base-directory"] == str(tmp_path / "dl")
    assert ex["archive"] == str(tmp_path / "data" / "archive.sqlite3")
    assert ex["cookies"] == "/c/cookies.txt"
    assert ex["sleep-request"] == [3.0, 5.0]
    assert ex["skip"] is True
    assert ex["directory"][0] == "{vanity}"
    assert s["output"]["mode"] is False


def test_complete_backfill_aborts_early(tmp_path):
    s = build_gdl_config(make_config(tmp_path), complete=True)
    assert s["extractor"]["skip"] == f"abort:{SKIP_ABORT_AFTER}"


def test_verbose_shows_gallery_dl_output(tmp_path):
    assert (
        build_gdl_config(make_config(tmp_path), verbose=True)["output"]["mode"]
        == "auto"
    )


def test_browser_cookies(tmp_path):
    cfg = make_config(tmp_path, login=Login("cookies_from_browser", browser="firefox"))
    assert build_gdl_config(cfg)["extractor"]["cookies"] == ["firefox"]


def test_passthrough_overrides_defaults(tmp_path):
    cfg = make_config(
        tmp_path,
        {
            "sleep-request": 1.0,
            "directory": ["{id}"],
            "patreon": {"files": ["images"]},
            "downloader": {"ytdl": {"format": "best"}},
            "output": {"progress": False},
        },
    )
    s = build_gdl_config(cfg, verbose=True)
    assert s["extractor"]["sleep-request"] == 1.0
    assert s["extractor"]["directory"] == ["{id}"]
    assert s["extractor"]["patreon"] == {"files": ["images"]}
    assert s["downloader"]["ytdl"]["format"] == "best"
    # nested tables merge rather than replace
    assert s["output"] == {"mode": "auto", "progress": False}
    # defaults the passthrough didn't touch are still there
    assert s["extractor"]["skip"] is True


def test_reserved_keys_always_win(tmp_path):
    # load_config strips these; build_gdl_config enforces them regardless
    cfg = make_config(tmp_path, {"archive": "/elsewhere", "cookies": "/other"})
    ex = build_gdl_config(cfg)["extractor"]
    assert ex["archive"] == str(tmp_path / "data" / "archive.sqlite3")
    assert ex["cookies"] == "/c/cookies.txt"


@pytest.mark.parametrize(
    "passthrough, expected",
    [
        ({}, True),
        ({"order-posts": "desc"}, True),
        ({"order-posts": "asc"}, False),
        ({"patreon": {"order-posts": "reverse"}}, False),
    ],
)
def test_post_order(tmp_path, passthrough, expected):
    s = build_gdl_config(make_config(tmp_path, passthrough))
    assert posts_are_newest_first(s) is expected


# ── PostStream ──────────────────────────────────────────────────────


def post(pid, date, **extra):
    return {"id": pid, "date": date, "title": f"post {pid}", **extra}


def files(p, *urls):
    msgs = [(Message.Directory, "", p)]
    for num, url in enumerate(urls, 1):
        msgs.append((Message.Url, url, {**p, "num": num}))
    return msgs


D1, D2, D3 = datetime(2024, 3, 1), datetime(2024, 2, 1), datetime(2024, 1, 1)


def urls(stream, messages):
    return [(m[1], m[2]["id"]) for m in stream.wrap(messages) if m[0] == Message.Url]


def test_stream_passes_posts_and_tags_vanity():
    s = PostStream("artist")
    out = list(s.wrap(files(post(1, D1), "https://a/1.jpg")))
    assert out[0][2]["vanity"] == "artist"
    assert s.counts.posts == 1


def test_stream_drops_locked_posts_and_counts_them():
    s = PostStream("artist")
    msgs = files(
        post(1, D1, current_user_can_view=False), "https://a/locked.jpg"
    ) + files(post(2, D2), "https://a/2.jpg")
    out = list(s.wrap(msgs))
    assert [m[2]["id"] for m in out if m[0] == Message.Directory] == [2]
    assert urls(PostStream("x"), msgs) == [("https://a/2.jpg", 2)]
    assert s.counts.locked == 1
    assert s.counts.posts == 1


def test_stream_stops_at_cutoff_when_newest_first():
    s = PostStream("artist", cutoff=datetime(2024, 1, 15))
    msgs = (
        files(post(1, D1), "u1") + files(post(2, D2), "u2") + files(post(3, D3), "u3")
    )
    msgs.append(("sentinel", "", {}))  # must never be reached
    assert urls(s, msgs) == [("u1", 1), ("u2", 2)]
    assert s.counts.reached_cutoff is True


def test_stream_skips_old_posts_when_oldest_first():
    s = PostStream("artist", cutoff=datetime(2024, 1, 15), newest_first=False)
    msgs = (
        files(post(3, D3), "u3") + files(post(2, D2), "u2") + files(post(1, D1), "u1")
    )
    assert urls(s, msgs) == [("u2", 2), ("u1", 1)]


def test_stream_keeps_posts_without_a_date():
    s = PostStream("artist", cutoff=datetime(2024, 1, 15))
    assert urls(s, files(post(1, None), "u1")) == [("u1", 1)]


def test_stream_skips_video_without_ffmpeg():
    s = PostStream("artist", allow_video=False)
    msgs = files(post(1, D1), "https://a/1.jpg", "ytdl:https://stream.mux.com/x.m3u8")
    assert urls(s, msgs) == [("https://a/1.jpg", 1)]
    assert s.counts.videos_skipped == 1


def test_stream_adds_embed_after_post_files():
    embed = {"url": "https://www.youtube.com/watch?v=abc", "provider": "YouTube"}
    s = PostStream("artist")
    msgs = files(
        post(1, D1, post_type="video_embed", embed=embed, _ytdl_manifest="hls"),
        "https://a/thumb.jpg",
    ) + files(post(2, D2), "https://a/2.jpg")
    out = [m for m in s.wrap(msgs) if m[0] == Message.Url]
    assert [m[1] for m in out] == [
        "https://a/thumb.jpg",
        "ytdl:https://www.youtube.com/watch?v=abc",
        "https://a/2.jpg",
    ]
    kw = out[1][2]
    assert kw["type"] == "embed" and kw["num"] == 0 and kw["extension"] == ""
    assert "_ytdl_manifest" not in kw


def test_stream_embed_on_last_post():
    embed = {"url": "https://vimeo.com/1"}
    s = PostStream("artist")
    msgs = files(post(1, D1, post_type="video_embed", embed=embed))
    assert urls(s, msgs) == [("ytdl:https://vimeo.com/1", 1)]


def test_stream_ignores_link_post_embeds():
    embed = {"url": "https://example.com/article"}
    s = PostStream("artist")
    assert urls(s, files(post(1, D1, post_type="link", embed=embed))) == []


def test_stream_skips_embed_without_ffmpeg():
    embed = {"url": "https://vimeo.com/1"}
    s = PostStream("artist", allow_video=False)
    assert urls(s, files(post(1, D1, post_type="video_embed", embed=embed))) == []
    assert s.counts.videos_skipped == 1


# ── ArchiveJob end to end, offline ──────────────────────────────────


class FakePatreonExtractor(Extractor):
    """Yields posts whose files are `text:` URLs, which gallery-dl writes
    to disk without any network access."""

    category = "patreon"
    subcategory = "creator"
    archive_fmt = "{id}_{num}"
    pattern = r"fake:(.*)"
    posts = []

    def items(self):
        for p in self.posts:
            p = dict(p)
            yield Message.Directory, "", p
            if not p.get("current_user_can_view", True):
                continue
            for num, (name, body) in enumerate(p.pop("_files"), 1):
                stem, _, ext = name.rpartition(".")
                p.update(
                    num=num, filename=stem, extension=ext, type=p.pop("_type", "image")
                )
                yield Message.Url, "text:" + body, p


def run_fake(cfg, posts, **stream_kw):
    apply_gdl_config(build_gdl_config(cfg))
    FakePatreonExtractor.posts = posts
    extr = FakePatreonExtractor(re.match(FakePatreonExtractor.pattern, "fake:x"))
    found = []
    job = ArchiveJob(
        extr, stream=PostStream("artist", **stream_kw), on_file=found.append
    )
    return job.run(), found


FAKE_POSTS = [
    {
        "id": 11,
        "date": datetime(2024, 5, 6),
        "title": "Hello: World?",
        "content": "<p>hi</p>",
        "_files": [("a.jpg", "AAA"), ("b.png", "BB")],
    },
    {
        "id": 12,
        "date": datetime(2024, 5, 1),
        "title": "",
        "content": None,
        "_files": [("notes.zip", "ZIP")],
        "_type": "attachment",
    },
    {
        "id": 13,
        "date": datetime(2024, 4, 1),
        "title": "Secret",
        "current_user_can_view": False,
        "_files": [],
    },
]


def test_job_writes_layout_metadata_and_reports_files(tmp_path):
    cfg = make_config(tmp_path)
    status, found = run_fake(cfg, FAKE_POSTS)
    assert status == 0

    creator_dir = tmp_path / "dl" / "artist"
    folders = sorted(p.name for p in creator_dir.iterdir())
    assert len(folders) == 2  # the locked post gets no folder
    first = creator_dir / next(f for f in folders if "[11]" in f)
    assert first.name.startswith("2024-05-06 Hello")
    assert (first / "01.jpg").read_text() == "AAA"
    assert (first / "02.png").read_text() == "BB"
    assert json.loads((first / "post.json").read_text())["id"] == 11
    assert "<p>hi</p>" in (first / "post.html").read_text()

    second = creator_dir / "2024-05-01 Untitled [12]"
    assert (second / "notes.zip").read_text() == "ZIP"
    assert "None" not in (second / "post.html").read_text()

    assert sorted((f.post_id, f.size) for f in found) == [
        ("11", 2),
        ("11", 3),
        ("12", 3),
    ]
    assert found[0].post_date == datetime(2024, 5, 6)


def test_job_second_run_downloads_nothing_even_if_files_moved(tmp_path):
    cfg = make_config(tmp_path)
    run_fake(cfg, FAKE_POSTS)
    for path in (tmp_path / "dl").rglob("*.jpg"):
        path.unlink()
    status, found = run_fake(cfg, FAKE_POSTS)
    assert status == 0
    assert found == []


# ── Chrome transport era additions ──────────────────────────────────


def test_youtube_solver_option(tmp_path):
    plain = build_gdl_config(make_config(tmp_path))
    assert "raw-options" not in plain["downloader"]["ytdl"]
    solver = build_gdl_config(make_config(tmp_path), youtube_solver=True)
    assert solver["downloader"]["ytdl"]["raw-options"] == {
        "remote_components": ["ejs:github"]
    }
    assert solver["downloader"]["ytdl"]["format"] == VIDEO_FORMAT


def test_video_prefers_h264(tmp_path):
    ytdl = build_gdl_config(make_config(tmp_path))["downloader"]["ytdl"]
    assert ytdl["format"].startswith("bv*[vcodec^=avc1]+ba")


def test_passthrough_can_change_video_format(tmp_path):
    cfg = make_config(tmp_path, {"downloader": {"ytdl": {"format": "bv*+ba/b"}}})
    ytdl = build_gdl_config(cfg, youtube_solver=True)["downloader"]["ytdl"]
    assert ytdl["format"] == "bv*+ba/b"
    assert "raw-options" in ytdl


CONTENT = (
    '<p>stream: <a href="https://youtu.be/PatronOnly1">https://youtu.be/PatronOnly1'
    "</a></p><p>also https://www.youtube.com/watch?feature=share&amp;v=AAAAAAAAAAA"
    " and https://youtube.com/shorts/BBBBBBBBBBB and again youtu.be/PatronOnly1</p>"
)


def test_stream_finds_youtube_links_in_text():
    s = PostStream("artist")
    msgs = files(post(1, D1, content=CONTENT), "https://a/1.jpg")
    out = [m for m in s.wrap(msgs) if m[0] == Message.Url]
    assert [m[1] for m in out] == [
        "https://a/1.jpg",
        "ytdl:https://www.youtube.com/watch?v=PatronOnly1",
        "ytdl:https://www.youtube.com/watch?v=AAAAAAAAAAA",
        "ytdl:https://www.youtube.com/watch?v=BBBBBBBBBBB",
    ]


def test_stream_link_fields():
    s = PostStream("artist")
    msgs = files(post(1, D1, content=CONTENT))
    kw = next(m[2] for m in s.wrap(msgs) if m[0] == Message.Url)
    assert kw["type"] == "link"
    assert kw["num"] == "yt-PatronOnly1"
    assert kw["link_id"] == "PatronOnly1"


def test_stream_embedded_video_is_not_also_a_link():
    embed = {"url": "https://www.youtube.com/watch?v=PatronOnly1"}
    s = PostStream("artist")
    msgs = files(
        post(
            1,
            D1,
            post_type="video_embed",
            embed=embed,
            content='<a href="https://youtu.be/PatronOnly1">watch</a>',
        )
    )
    assert [u for u, _ in urls(s, msgs)] == [
        "ytdl:https://www.youtube.com/watch?v=PatronOnly1"
    ]


def test_stream_links_skipped_without_ffmpeg():
    s = PostStream("artist", allow_video=False)
    assert urls(s, files(post(1, D1, content=CONTENT))) == []
    assert s.counts.videos_skipped == 3


def link_post(content):
    return [
        {
            "id": 21,
            "date": datetime(2024, 6, 1),
            "title": "Stream",
            "content": content,
            "_files": [("cover.png", "PNG")],
        }
    ]


def run_links(cfg, posts, availability):
    """Run the fake job; yt-dlp is replaced by a probe and a fake download."""
    apply_gdl_config(build_gdl_config(cfg))
    FakePatreonExtractor.posts = posts
    extr = FakePatreonExtractor(re.match(FakePatreonExtractor.pattern, "fake:x"))
    probed, found = [], []

    def probe(url):
        probed.append(url)
        if availability is None:
            return None, None
        return "instance", {"availability": availability}

    job = ArchiveJob(
        extr, stream=PostStream("artist"), on_file=found.append, probe_link=probe
    )
    real_download = job.download

    def download(url):
        if not url.startswith("ytdl:"):
            return real_download(url)
        pathfmt = job.pathfmt
        assert pathfmt.kwdict["_ytdl_info_dict"]["availability"] == availability
        pathfmt.set_extension("mp4")
        pathfmt.build_path()
        with pathfmt.open("wb") as fp:
            fp.write(b"VIDEO")
        return True

    job.download = download
    status = job.run()
    return status, job, probed, found


def test_unlisted_link_is_downloaded(tmp_path):
    cfg = make_config(tmp_path)
    status, job, probed, found = run_links(
        cfg, link_post('<a href="https://youtu.be/PatronOnly1">x</a>'), "unlisted"
    )
    assert status == 0
    assert probed == ["https://www.youtube.com/watch?v=PatronOnly1"]
    video = tmp_path / "dl/artist/2024-06-01 Stream [21]/youtube-PatronOnly1.mp4"
    assert video.read_bytes() == b"VIDEO"
    assert len(found) == 2

    # the next run neither probes nor downloads it again
    status, job, probed, found = run_links(
        cfg, link_post('<a href="https://youtu.be/PatronOnly1">x</a>'), "unlisted"
    )
    assert probed == [] and found == []


def test_public_link_is_left_alone_and_remembered(tmp_path):
    cfg = make_config(tmp_path)
    status, job, probed, found = run_links(
        cfg, link_post("https://youtu.be/PatronOnly1"), "public"
    )
    assert status == 0
    assert job.public_links_skipped == 1
    assert [f.path.rsplit("/", 1)[1] for f in found] == ["01.png"]

    _, job, probed, _ = run_links(
        cfg, link_post("https://youtu.be/PatronOnly1"), "public"
    )
    assert probed == []


def test_unreadable_link_is_a_warning_not_a_failure(tmp_path):
    cfg = make_config(tmp_path)
    status, job, probed, found = run_links(
        cfg, link_post("https://youtu.be/PatronOnly1"), None
    )
    assert status == 0
    assert job.video_failures == ["https://www.youtube.com/watch?v=PatronOnly1"]
    # not remembered, so it is tried again next run
    _, _, probed, _ = run_links(cfg, link_post("https://youtu.be/PatronOnly1"), None)
    assert probed == ["https://www.youtube.com/watch?v=PatronOnly1"]


class FakeApiExtractor:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.urls = data, error, []

    def request_json(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.data


def test_lookup_campaign_id():
    extr = FakeApiExtractor({"data": [{"id": "12345678", "type": "campaign"}]})
    assert lookup_campaign_id("somecreator", extr) == "12345678"
    assert "filter[vanity]=somecreator" in extr.urls[0]


def test_lookup_unknown_creator():
    with pytest.raises(LookupError, match="no Patreon creator named 'nobody'"):
        lookup_campaign_id("nobody", FakeApiExtractor({"data": []}))


def test_lookup_http_error():
    with pytest.raises(LookupError, match="could not look up"):
        lookup_campaign_id("x", FakeApiExtractor(error=OSError("403 Forbidden")))
