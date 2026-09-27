"""Everything that touches gallery-dl.

gallery-dl does the Patreon work. PatronStash configures it, routes its
www.patreon.com traffic through a Chrome-like transport (see transport.py),
filters the stream of posts its Patreon extractor produces (locked posts,
backfill cutoff, video without ffmpeg, embeds and unlisted YouTube links),
and records every finished download in stats.db. The only Patreon API calls
PatronStash makes itself are the login check and looking up each creator's
campaign ID, because Cloudflare blocks the creator page gallery-dl would
otherwise read it from.
"""

from __future__ import annotations

import collections
import copy
import html
import logging
import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from gallery_dl import config as gdl_config
from gallery_dl import extractor as gdl_extractor
from gallery_dl import util as gdl_util
from gallery_dl import ytdl as gdl_ytdl
from gallery_dl.extractor.message import Message
from gallery_dl.job import DownloadJob

from .config import Config, Creator
from .progress import PostReporter, ProgressBar
from .transport import DEFAULT_API_DELAY, set_api_delay, use_chrome_transport

log = logging.getLogger("patronstash")

# Once a creator's backfill is complete, stop after this many consecutive
# already-downloaded files instead of walking their whole history again.
SKIP_ABORT_AFTER = 20

# Passthrough keys that live at the root of gallery-dl's config; everything
# else in [gallery-dl] is an extractor option.
ROOT_SECTIONS = ("downloader", "output", "cache", "postprocessor")

ASCENDING_ORDERS = ("a", "asc", "r", "reverse")
# gallery-dl reads an extractor option from these levels, deepest winning:
# [gallery-dl], [gallery-dl.patreon] and [gallery-dl.patreon.creator].
OPTION_LEVELS = ("patreon", "creator")
API = "https://www.patreon.com/api/"
CURRENT_USER_URL = API + "current_user?json-api-version=1.0"
CAMPAIGN_URL = API + "campaigns?filter[vanity]={}&json-api-version=1.0"

YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?(?:[^\s\"'<>]*?&)?v=|shorts/|live/|embed/)"
    r"|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)
# yt-dlp availability values that mean "not public": links like these in a
# post are most likely the creator's patron-only videos.
NOT_PUBLIC = {"unlisted", "private", "needs_auth", "subscriber_only", "premium_only"}

# Prefer H.264 video: it plays on any device. yt-dlp's own default prefers
# AV1, which is smaller but needs recent hardware to play smoothly. Falls
# back to the best of any codec when there's no H.264 version.
VIDEO_FORMAT = "bv*[vcodec^=avc1]+ba/b[vcodec^=avc1]/bv*+ba/b"


def tool_available(name: str) -> bool:
    """Whether an external program is on PATH."""
    return shutil.which(name) is not None


def ffmpeg_available() -> bool:
    return tool_available("ffmpeg")


def deno_available() -> bool:
    return tool_available("deno")


def _post_date(kwdict: dict) -> datetime | None:
    """A post's date; gallery-dl marks a missing one with a falsy datetime."""
    date = kwdict.get("date")
    return date if isinstance(date, datetime) and date else None


def extractor_option(options: dict, key: str, default=None):
    """Look up an extractor option the way gallery-dl does for a creator:
    the deepest of the root, `patreon` and `patreon.creator` levels wins."""
    value = options.get(key, default)
    for level in OPTION_LEVELS:
        options = options.get(level)
        if not isinstance(options, dict):
            break
        value = options.get(key, value)
    return value


def _zero_sleep_request(options: dict) -> None:
    """Turn off gallery-dl's own sleep-request at every level it's set."""
    for key, value in options.items():
        if key == "sleep-request":
            options[key] = 0
        elif isinstance(value, dict):
            _zero_sleep_request(value)


def api_delay(cfg: Config):
    """The configured wait between API requests, in any gallery-dl form."""
    return extractor_option(cfg.passthrough, "sleep-request", DEFAULT_API_DELAY)


def posts_are_newest_first(options: dict) -> bool:
    return extractor_option(options, "order-posts") not in ASCENDING_ORDERS


def build_gdl_config(
    cfg: Config,
    *,
    complete: bool = False,
    verbose: bool = False,
    youtube_solver: bool = False,
) -> dict:
    """The full gallery-dl config for one creator's run.

    Priority, lowest first: PatronStash defaults, the [gallery-dl]
    passthrough, then the reserved keys PatronStash depends on.

    `complete` (the creator's backfill is done) makes the run stop after
    SKIP_ABORT_AFTER already-downloaded files, when posts come newest first.

    `youtube_solver` lets yt-dlp fetch its challenge-solver script from
    GitHub, which it runs with deno to unlock YouTube's full-quality formats.
    """
    # A copy, because merging and the sleep-request handling below modify it.
    passthrough = copy.deepcopy(cfg.passthrough)
    root_part = {k: v for k, v in passthrough.items() if k in ROOT_SECTIONS}
    extractor_part = {k: v for k, v in passthrough.items() if k not in ROOT_SECTIONS}
    stop_early = complete and posts_are_newest_first(extractor_part)

    extractor = {
        "base-directory": str(cfg.download_dir),
        "directory": ["{vanity}", "{date:%Y-%m-%d} {title[:100]|'Untitled'} [{id}]"],
        "filename": {
            "type == 'attachment'": "{filename}.{extension}",
            "type == 'embed'": "embed.{extension}",
            "type == 'link'": "youtube-{link_id}.{extension}",
            "": "{num:>02}.{extension}",
        },
        "skip": f"abort:{SKIP_ABORT_AFTER}" if stop_early else True,
        "postprocessors": [
            {"name": "metadata", "event": "post", "filename": "post.json"},
            {
                "name": "metadata",
                "event": "post",
                "mode": "custom",
                "filename": "post.html",
                "content-format": "<meta charset=\"utf-8\">\n{content|''}\n",
            },
        ],
    }
    result = {
        "extractor": extractor,
        "downloader": {"ytdl": {"format": VIDEO_FORMAT}},
        "output": {"mode": "auto" if verbose else False},
        "cache": {"file": str(cfg.data_dir / "gallery-dl-cache.sqlite3")},
    }

    if youtube_solver:
        result["downloader"]["ytdl"]["raw-options"] = {
            "remote_components": ["ejs:github"]
        }

    gdl_util.combine_dict(result, root_part)
    gdl_util.combine_dict(extractor, extractor_part)

    # transport.py applies the API delay instead (see api_delay); gallery-dl's
    # own would also slow down requests to Patreon's file server.
    _zero_sleep_request(extractor)
    extractor["sleep-request"] = 0

    extractor["archive"] = str(cfg.data_dir / "archive.sqlite3")
    if cfg.login is not None:
        extractor["cookies"] = cfg.login.gallery_dl_cookies()
    return result


def apply_gdl_config(settings: dict) -> None:
    gdl_config.clear()
    for key, value in settings.items():
        gdl_config.set((), key, value)


def configure(cfg: Config, **options) -> dict:
    """Build and apply the gallery-dl config, and set the API delay."""
    settings = build_gdl_config(cfg, **options)
    apply_gdl_config(settings)
    set_api_delay(api_delay(cfg))
    return settings


# ── the message filter ──────────────────────────────────────────────


@dataclass
class StreamCounts:
    locked: int = 0
    videos_skipped: int = 0


class PostStream:
    """Filters and extends the messages from gallery-dl's Patreon extractor.

    - drops locked posts entirely, so no folder is created for them
    - drops posts older than the backfill cutoff; when posts arrive newest
      first, the first older post ends the run for this creator
    - drops video (anything yt-dlp would fetch) when ffmpeg is missing
    - adds a download for a post's external embed (YouTube, Vimeo, …)
    - adds a candidate download for each YouTube link in the post text;
      ArchiveJob keeps only the ones that aren't public
    """

    def __init__(
        self,
        *,
        cutoff: datetime | None = None,
        newest_first: bool = True,
        allow_video: bool = True,
    ):
        self.cutoff = cutoff
        self.newest_first = newest_first
        self.allow_video = allow_video
        self.counts = StreamCounts()

    def _too_old(self, post: dict) -> bool:
        date = _post_date(post)
        return bool(self.cutoff and date and date < self.cutoff)

    def _videos(self, post: dict | None):
        """Downloads to add after a post's own files: embed, then links."""
        if post is None:
            return
        found = []
        embed = post.get("embed")
        embed_url = embed.get("url") if isinstance(embed, dict) else None
        if embed_url and str(post.get("post_type", "")).endswith("_embed"):
            found.append(("embed", embed_url, {"num": 0, "file": embed}))

        seen = set(YOUTUBE_RE.findall(embed_url or ""))
        for video_id in YOUTUBE_RE.findall(html.unescape(post.get("content") or "")):
            if video_id not in seen:
                seen.add(video_id)
                url = "https://www.youtube.com/watch?v=" + video_id
                found.append(
                    ("link", url, {"num": "yt-" + video_id, "link_id": video_id})
                )

        for kind, url, extra in found:
            if not self.allow_video:
                self.counts.videos_skipped += 1
                continue
            for key in ("_ytdl_manifest", "_ytdl_manifest_headers", "_ytdl_extra"):
                post.pop(key, None)
            post.update(type=kind, hash="", filename=kind, extension="", **extra)
            yield Message.Url, "ytdl:" + url, post

    def wrap(self, messages):
        current = None  # the post whose files are being passed through
        for message in messages:
            kind, url, kwdict = message

            if kind == Message.Directory:
                yield from self._videos(current)
                current = None
                if self._too_old(kwdict):
                    if self.newest_first:
                        return
                    continue
                if not kwdict.get("current_user_can_view", True):
                    self.counts.locked += 1
                    continue
                current = kwdict
                yield message

            elif kind == Message.Url:
                if current is None:
                    continue
                if url.startswith("ytdl:") and not self.allow_video:
                    self.counts.videos_skipped += 1
                    continue
                yield message

            elif current is not None:
                yield message

        yield from self._videos(current)


# ── the job ─────────────────────────────────────────────────────────


@dataclass
class DownloadedFile:
    post_id: str
    post_date: datetime | None
    path: str
    size: int


class ArchiveJob(DownloadJob):
    """A gallery-dl DownloadJob that runs messages through a PostStream,
    reports every finished file, downloads YouTube links only when they
    aren't public, and treats failed embeds and links as warnings."""

    def __init__(
        self,
        url,
        parent=None,
        *,
        stream=None,
        on_file=None,
        probe_link=None,
        reporter=None,
        out=None,
        vanity=None,
    ):
        DownloadJob.__init__(self, url, parent)
        if vanity is not None:
            # The `{vanity}` field of every post. The job's own keywords are
            # applied after any the user configures, so this always wins.
            self.kwdict["vanity"] = vanity
        self.stream = stream
        self.on_file = on_file
        self.reporter = reporter
        if out is not None:
            self.out = out  # downloaders pick this up when they're created
        self.probe_link = probe_link or self._probe_link
        self._link_ytdl = None
        self.video_failures: list[str] = []
        self.public_links_skipped = 0

    def dispatch(self, messages):
        if self.stream is not None:
            messages = self.stream.wrap(messages)
        return DownloadJob.dispatch(self, messages)

    def handle_directory(self, kwdict):
        if self.reporter is not None:
            self.reporter.post_started(kwdict)
        DownloadJob.handle_directory(self, kwdict)

    def handle_finalize(self):
        if self.reporter is not None:
            self.reporter.finish_post()
        DownloadJob.handle_finalize(self)

    def initialize(self, kwdict=None):
        DownloadJob.initialize(self, kwdict)
        if self.on_file is not None or self.reporter is not None:
            if not isinstance(self.hooks, collections.defaultdict):
                self.hooks = collections.defaultdict(list, self.hooks or {})
            self.hooks["after"].append(self._file_done)

    def _file_done(self, pathfmt):
        kwdict = pathfmt.kwdict
        try:
            size = os.path.getsize(pathfmt.realpath)
        except OSError:
            size = 0
        if self.reporter is not None:
            self.reporter.file_done()
        if self.on_file is None:
            return
        self.on_file(
            DownloadedFile(
                post_id=str(kwdict.get("id")),
                post_date=_post_date(kwdict),
                path=pathfmt.realpath,
                size=size,
            )
        )

    def handle_url(self, url, kwdict):
        kind = kwdict.get("type")
        if kind == "link":
            return self._handle_link(url, kwdict)
        if kind == "embed":
            return self._handle_optional(url, kwdict)
        return DownloadJob.handle_url(self, url, kwdict)

    def _handle_optional(self, url, kwdict):
        # A dead YouTube video shouldn't fail the whole creator every run.
        status = self.status
        DownloadJob.handle_url(self, url, kwdict)
        if self.status != status:
            self.status = status
            self._video_failed(url, kwdict)

    def _video_failed(self, url, kwdict):
        video_url = url.removeprefix("ytdl:")
        self.video_failures.append(video_url)
        log.warning(
            "post %s: could not download %s %s",
            kwdict.get("id"),
            kwdict["type"],
            video_url,
        )

    def _handle_link(self, url, kwdict):
        if self.archive is not None and self.archive.check(kwdict):
            return DownloadJob.handle_url(self, url, kwdict)  # already have it
        video_url = url.removeprefix("ytdl:")
        try:
            ytdl_instance, info = self.probe_link(video_url)
        except Exception as exc:
            log.debug("probing %s: %s: %s", video_url, exc.__class__.__name__, exc)
            info = None
        if not info:
            return self._video_failed(url, kwdict)

        availability = info.get("availability")
        if availability not in NOT_PUBLIC:
            # Public videos are left alone, and remembered so that later
            # runs don't ask YouTube again.
            log.info(
                "post %s: skipping %s YouTube link %s",
                kwdict.get("id"),
                availability or "public",
                video_url,
            )
            self.public_links_skipped += 1
            if self.archive is not None:
                self.archive.add(kwdict)
            return

        kwdict["_ytdl_instance"] = ytdl_instance
        kwdict["_ytdl_info_dict"] = info
        try:
            self._handle_optional(url, kwdict)
        finally:
            # The post dict is reused for the post's next link.
            kwdict.pop("_ytdl_instance", None)
            kwdict.pop("_ytdl_info_dict", None)

    def _probe_link(self, url):
        """Ask yt-dlp about a video, using gallery-dl's yt-dlp settings."""
        if self._link_ytdl is None:
            downloader = self.get_downloader("ytdl")
            module = gdl_ytdl.import_module(downloader.config("module"))
            self._link_ytdl = gdl_ytdl.construct_YoutubeDL(
                module, downloader, downloader.ytdl_opts
            )
        return self._link_ytdl, self._link_ytdl.extract_info(url, download=False)


@dataclass
class CreatorRun:
    status: int = 0
    files: list[DownloadedFile] = field(default_factory=list)
    counts: StreamCounts = field(default_factory=StreamCounts)
    video_failures: list[str] = field(default_factory=list)
    public_links_skipped: int = 0

    @property
    def ok(self) -> bool:
        return self.status == 0

    @property
    def new_posts(self) -> int:
        return len({f.post_id for f in self.files})

    @property
    def new_bytes(self) -> int:
        return sum(f.size for f in self.files)


def run_creator(
    cfg: Config,
    creator: Creator,
    *,
    cutoff: datetime | None,
    complete: bool,
    allow_video: bool,
    verbose: bool = False,
    on_file=None,
    progress=None,
) -> CreatorRun:
    """Download one creator. `on_file` is called for each finished file.

    `progress` replaces the terminal progress bar: any object with
    ProgressBar's methods (start, progress, success, skip, clear) and usable
    as a context manager. `patronstash watch` passes its dashboard's.
    """
    settings = configure(
        cfg, complete=complete, verbose=verbose, youtube_solver=deno_available()
    )

    result = CreatorRun()
    try:
        campaign_id = lookup_campaign_id(creator.name, _api_extractor(cfg))
    except LookupError as exc:
        log.error("%s: %s", creator.name, exc)
        result.status = 1
        return result
    stream = PostStream(
        cutoff=cutoff,
        newest_first=posts_are_newest_first(settings["extractor"]),
        allow_video=allow_video,
    )

    def file_done(downloaded: DownloadedFile):
        result.files.append(downloaded)
        if on_file is not None:
            on_file(downloaded)

    extr = gdl_extractor.find(f"https://www.patreon.com/id:{campaign_id}")
    use_chrome_transport(extr)
    # With -v, gallery-dl's own per-file output and progress are shown instead.
    bar = (
        progress
        if progress is not None
        else ProgressBar(enabled=False if verbose else None)
    )
    job = ArchiveJob(
        extr,
        stream=stream,
        on_file=file_done,
        reporter=PostReporter(creator.name),
        out=None if verbose else bar,
        vanity=creator.name,
    )
    with bar:
        result.status = job.run()
    result.counts = stream.counts
    result.video_failures = job.video_failures
    result.public_links_skipped = job.public_links_skipped
    return result


_api_extractors: dict[str, object] = {}


def _api_extractor(cfg: Config):
    """A gallery-dl Patreon extractor for API requests, with the configured
    cookies and the Chrome transport. The login check and every creator's
    lookup in a run share it; close_api_extractors() ends it."""
    login = cfg.login.gallery_dl_cookies() if cfg.login else None
    key = repr((login, str(cfg.data_dir), cfg.passthrough))
    if key not in _api_extractors:
        extr = gdl_extractor.find("https://www.patreon.com/home")
        with _quiet_cookie_warning():
            use_chrome_transport(extr)
        _api_extractors[key] = extr
    return _api_extractors[key]


def close_api_extractors() -> None:
    """Close the shared API connections; call when a run or check ends."""
    for extr in _api_extractors.values():
        extr.session.close()
    _api_extractors.clear()


def lookup_campaign_id(vanity: str, extr) -> str:
    """Find a creator's campaign ID from their vanity name.

    gallery-dl would read it from the creator's page, but Cloudflare
    challenges that page even with the Chrome transport; the API isn't.
    """
    try:
        data = extr.request_json(CAMPAIGN_URL.format(vanity))
    except Exception as exc:
        raise LookupError(
            f"could not look up the creator on Patreon ({exc.__class__.__name__}: "
            f"{exc})"
        ) from exc
    for campaign in data.get("data") or ():
        if campaign.get("type") == "campaign" and campaign.get("id"):
            return str(campaign["id"])
    raise LookupError(
        f"no Patreon creator named '{vanity}'; check the name in the config"
    )


# ── login ───────────────────────────────────────────────────────────


@dataclass
class LoginResult:
    ok: bool
    message: str


@contextmanager
def _quiet_cookie_warning():
    """Hide gallery-dl's "no 'session_id' cookie set"; check_login says more."""

    def drop(record):
        return "session_id" not in record.getMessage()

    logger = logging.getLogger("patreon")
    logger.addFilter(drop)
    try:
        yield
    finally:
        logger.removeFilter(drop)


def check_login(cfg: Config) -> LoginResult:
    """Confirm the configured cookies belong to a logged-in Patreon session.

    With expired cookies gallery-dl would quietly carry on as a logged-out
    visitor, so every run checks this before downloading anything.
    """
    login = cfg.login
    if login is None:
        return LoginResult(
            False, "no login configured (set cookies_file or cookies_from_browser)"
        )
    if login.method == "cookies_file" and not login.cookies_file.is_file():
        return LoginResult(False, f"cookies file not found: {login.cookies_file}")

    again = " and export cookies.txt again" if login.method == "cookies_file" else ""

    configure(cfg)
    try:
        extr = _api_extractor(cfg)
        if not extr.cookies_check(("session_id",), subdomains=True):
            return LoginResult(
                False,
                f"no Patreon session cookie found via {login.describe()}; "
                f"log in to Patreon in your browser{again}",
            )
        response = extr.request(CURRENT_USER_URL, fatal=False)
    except Exception as exc:
        return LoginResult(
            False, f"could not check the login: {exc.__class__.__name__}: {exc}"
        )

    if response.status_code in (401, 403):
        return LoginResult(
            False,
            f"the Patreon session from {login.describe()} has expired or was "
            f"logged out; log in again{again}",
        )
    if response.status_code != 200:
        return LoginResult(
            False, f"Patreon answered HTTP {response.status_code} to the login check"
        )
    try:
        user = response.json()["data"]["attributes"]
        who = user.get("full_name") or user.get("vanity") or "unknown user"
    except Exception:
        who = "unknown user"
    return LoginResult(True, f"logged in as {who} via {login.describe()}")
