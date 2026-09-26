# PatronStash — Design

PatronStash is an unofficial tool that automatically archives content from the Patreon creators you support: posts, images, videos, attachments, and the post text. It exists so that your paid content survives a lapsed subscription or a deleted post.

It is not affiliated with Patreon.

This document records the design decisions made before any code was written (2026-09-26). Each section gives the decision and, where it wasn't obvious, why.

---

## 1. Scope and shape

- **Purpose:** a personal archive, run on a schedule, that downloads only what's new on each run.
- **Audience:** mainly archivists, but simple enough for casual users who just want "save my Patreon stuff".
- **Architecture:** a thin wrapper around [`gallery-dl`](https://github.com/mikf/gallery-dl), with [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) for video. PatronStash adds the config, creator list, safety checks, stats, logging and notifications. It does **not** talk to Patreon's API itself.
  - *Why:* `gallery-dl`'s maintainers already track Patreon's (undocumented) API changes. Reimplementing that would make PatronStash break every time Patreon changes something.
- **Exception: getting past Cloudflare** (decided 2026-09-26, after the first live test). Since about August 2026, Cloudflare has answered every request from `gallery-dl` to Patreon with 403, whatever the cookies and headers ([gallery-dl #9371](https://github.com/mikf/gallery-dl/issues/9371)). It recognises the client by its TLS/HTTP2 fingerprint. So PatronStash:
  - sends `gallery-dl`'s `www.patreon.com` requests through [`curl_cffi`](https://github.com/lexiforest/curl_cffi), which has Chrome's fingerprint. Downloads from Patreon's CDN aren't challenged and still go through `gallery-dl` normally.
  - looks up each creator's campaign ID through Patreon's API (`/api/campaigns?filter[vanity]=…`) and hands `gallery-dl` a `patreon.com/id:<campaign>` URL. `gallery-dl` would normally read the ID from the creator's HTML page, but Cloudflare challenges that page even with the Chrome fingerprint, while the API gets through.
  - *Why:* without this, PatronStash can't download anything. It is the smallest change that works: `gallery-dl` still does all the post and file work. Once `gallery-dl` handles this itself, the workaround can be removed.
- **License:** GPL-2.0-or-later.
  - *Why:* `gallery-dl` is GPL-2.0-only, and PatronStash imports it as a library. Note that **GPL-3.0 would be incompatible** with it.
- **Python:** 3.11 or newer, because 3.11 is the first version that reads TOML (`tomllib`) without extra packages.

## 2. Installation

- The project is an installable package (`pyproject.toml`) that provides a `patronstash` command.
- Users install it with `pipx install git+https://github.com/reveler-hub/PatronStash`, or with `pip install` inside a venv.
- For development, use an editable install inside the repo's `.venv`.
- It is not published to PyPI in v1 (see the roadmap).

## 3. Command-line interface

| Command | What it does |
|---|---|
| `patronstash` | Shows help. It never starts a download by accident. |
| `patronstash run` | Does one pass over every creator and then exits. Cron and systemd call this. |
| `patronstash check` | Downloads nothing. Validates the setup and prints a ✅ / ⚠ / ❌ list (details below). |
| `patronstash status` | Shows one line per creator: backfill setting and progress, number of posts and files, size on disk, date of the last new post, and the time and result of the last run. |

Global options:

- `--config PATH` points at a different config file.
- `-v` shows `gallery-dl`'s full per-file output.

`check` verifies each of the following:

- The TOML file parses.
- Every creator has a `backfill` line.
- The login works, and it reports which login method is in use.
- `ffmpeg` is installed.
- No reserved keys are set in the passthrough section (see §5).
- If notifications are configured, a test notification gets through.

## 4. Files and locations

| What | Default location |
|---|---|
| Config | `~/.config/patronstash/config.toml` (override with `--config`) |
| Data: download archive, `stats.db`, log, lock file | `~/.local/share/patronstash/` (override with `data_dir`) |
| Downloads | `download_dir`, which is **required** and has no default |

`download_dir` has no default on purpose. A single backfill can be hundreds of GB, so the user should decide where it goes rather than having their home drive fill up by surprise.

## 5. Config file

The config file is TOML. It opens with a commented example, and that example spells out the risks of backfilling (see §6.2).

```toml
# ── PatronStash config ─────────────────────────────────────────────
# Add one [[creator]] block per creator. EVERY creator needs a
# `backfill` line; a creator without one is skipped (with a warning).
#
#   backfill = "all"         download their ENTIRE post history
#   backfill = "none"        only posts published from now on
#   backfill = "2024-01-01"  everything since that date
#
# ⚠ "all" can mean thousands of posts and tens of GB (video especially).
# ⚠ The first sync can take hours or days and span several runs.
# ⚠ It sends many requests to Patreon; PatronStash waits between them,
#   but very large backfills may still get throttled.
#
# Example:
#   [[creator]]
#   name = "somecreator"
#   backfill = "none"
# ───────────────────────────────────────────────────────────────────

download_dir = "/mnt/archive/patreon"

# Login: set one or both. If both are set, cookies_file wins.
cookies_file = "~/.config/patronstash/cookies.txt"
# cookies_from_browser = "chrome"       # desktop only, not headless boxes
# browser_profile = "Profile 1"         # optional; Chrome: "Default", "Profile 1"…
#                                       # (see chrome://version); Firefox: "default-release"

# Optional notifications (any Apprise URL: ntfy, Discord, Telegram, email…)
# notify_url = "ntfy://ntfy.sh/my-topic"
# notify_summary = false                # true = also notify after successful runs

# data_dir = "~/.local/share/patronstash"

[[creator]]
name = "somecreator"          # vanity name: patreon.com/somecreator
backfill = "all"

[[creator]]
name = "othercreator"
backfill = "2024-01-01"

# Passed straight to gallery-dl; overrides PatronStash's defaults.
[gallery-dl]
# e.g. directory / filename templates, sleep-request, video options…
```

Rules for the config:

- **Creators:** the user edits the file to add them; there is no `add` command. A `name` must be the creator's **vanity name** (decided 2026-09-26). A URL is rejected with an error that names the vanity name to use instead.
  - *Why:* a list of bare names is easier to read than a list of URLs that differ only at the end.
- **Missing `backfill`:** that creator is skipped. PatronStash logs a warning and sends a notification that names the creator, and every other creator still runs.
- **The `[gallery-dl]` passthrough** can override anything except these reserved keys, which PatronStash depends on:
  - `archive`
  - `cookies`
  - the stats hook that PatronStash attaches to each download

  If a reserved key appears there, PatronStash ignores it and prints a warning, and `check` flags it. Settings that users legitimately want to change, such as where the data lives, are PatronStash options (`data_dir`), not passthrough keys.

## 6. Behaviour

### 6.1 Login

- There are two supported methods:
  - **`cookies_file`**: a `cookies.txt` file exported with a browser extension such as "Get cookies.txt LOCALLY". It works everywhere, including headless boxes.
  - **`cookies_from_browser`**: reads the cookies straight from a browser on the same machine, using `gallery-dl`'s built-in support. Supported browsers are Firefox, LibreWolf, Zen, Floorp, Chrome, Chromium, Brave, Edge, Opera, Vivaldi and Thorium. It only works on a desktop. On Linux, Chromium-based browsers need the system keyring (GNOME Keyring or KWallet), and this can fail on some setups; Firefox-family browsers need nothing extra.
- If both methods are set, **`cookies_file` takes precedence.** Every `run` log and `check` report states which method is actually in use.
- **The login is checked before every run.** If the session has expired, nothing is downloaded, and PatronStash logs an error, sends a notification and exits with a non-zero code.
  - *Why:* with expired cookies, `gallery-dl` can quietly carry on as a logged-out visitor. The archive would then silently stop receiving paid content.

### 6.2 What gets downloaded

- Only the creators in the config list are downloaded.
- **Backfill** is set per creator: `all`, `none` or a cutoff date. If a large backfill is interrupted, the next run resumes it, because the download archive (§6.3) skips anything already fetched.
- Post text and metadata: see the layout in §6.4.
- Patreon-hosted video is downloaded with `yt-dlp` and joined with `ffmpeg`.
  - If `ffmpeg` is missing, PatronStash warns once and skips video, and everything else still downloads.
- External embeds such as YouTube, Vimeo and SoundCloud are **downloaded** too.
- **YouTube links in the post text** (decided 2026-09-26) are downloaded only if yt-dlp reports the video as **unlisted or private**. Creators often share patron-only videos this way (as unlisted YouTube links), whereas public links are usually their public uploads or other people's videos. Public links are left alone and remembered, so later runs don't ask YouTube about them again. A link that can't be read is logged as a warning and retried on the next run. Failed embeds are treated the same way, so one dead video doesn't fail the creator on every run.
- **YouTube quality** (decided 2026-09-26): if `deno` is installed, PatronStash lets yt-dlp fetch its challenge-solver script from GitHub, which unlocks YouTube's full-quality formats. Without `deno`, YouTube video may be limited to lower quality, and `check` warns about it.
- **Video codec** (decided 2026-09-26): prefer **H.264**, falling back to the best of any codec when there's no H.264 version. yt-dlp's own default prefers AV1, which is about half the size at the same resolution but needs fairly recent hardware to play smoothly. For an archive, playing everywhere matters more. This can be overridden with `[gallery-dl.downloader.ytdl] format`.
- **Locked posts** (tiers you don't pay for) are skipped entirely: no folder is created. The log records how many were skipped for each creator. If you later upgrade your tier, the next run downloads them.
- **Rate limiting:** a random delay of **3–5 seconds** between API requests. File downloads from the CDN have no delay. The delay can be changed through the passthrough.

### 6.3 Incremental runs

- `gallery-dl`'s SQLite **download archive** is stored in `data_dir`. It records every item it has downloaded by Patreon ID and skips those items on later runs, even if the files have since been moved or renamed.
- **Finished backfills stop early** (decided 2026-09-26). Once a creator's backfill is complete, each run stops after **20 files in a row** that are already in the archive, instead of walking the whole post history again. Walking everything would be slow: each attachment costs another request with the 3–5 second delay.
- **Edited posts:** because of that early stop, a new attachment on a recent post gets downloaded, but one added to an *old* post, beyond the 20-file window, does not. If a creator *replaces* a file under the same ID, the change is not detected either. `rescan` on the roadmap would handle both.
- **Lock file:** runs never overlap. If a run starts while another is still going (for example, a long backfill that outlasts the schedule interval), it logs "already running" and exits.

### 6.4 Default layout on disk

```
<download_dir>/<vanity-name>/<YYYY-MM-DD> <post title> [<post id>]/
    01.jpg  02.jpg  video.mp4  attachment.zip
    post.json    ← full metadata (tags, tier, date, URL)
    post.html    ← the post's text/body
```

- Creator folders are named after the **vanity name**, not the display name.
  - *Why:* display names change, which would split one creator's archive across two folders.
- Users can override the whole layout through the `[gallery-dl]` passthrough (`directory`, `filename`, postprocessors).

## 7. State, logging and notifications

- **`stats.db`** (SQLite, in `data_dir`) has two parts:
  - one row per downloaded file: creator, post ID, post date, file size and download time
  - a per-creator table: last run time, last result and backfill state

  `status` reads from it.
  - *Why:* `gallery-dl`'s archive stores only IDs, with no sizes, dates or totals. Keeping one row per file also means future features won't need a data migration.
- **Logging:**
  - a rotating `patronstash.log` in `data_dir` (5 files × 5 MB)
  - console output by default (decided 2026-09-26, replacing "a summary line per creator"):
    - a line when each creator starts ("checking for new posts…")
    - one line per post that had new files
    - a heartbeat every 50 posts checked during long backfills
    - on a terminal only, a live progress bar for downloads that take more than a few seconds (videos, big attachments)
    - the summary line per creator, plus warnings and errors
    - *Why:* with only a summary line per creator, a run showed nothing at all for hours during a backfill or a big video, so users couldn't tell it was working. The post lines go to the log file too. The progress bar is left out under cron and systemd, where nobody is watching.
  - `-v` for full detail: gallery-dl's own per-file output instead of the progress bar
  - *Why:* cron throws away output unless mail is set up, so a log file is the only reliable record on a headless box.
- **Notifications:** optional, through [Apprise](https://github.com/caronc/apprise), configured with one `notify_url`. By default they're sent only on failure: expired login, a creator missing its `backfill` line, or a run error. Setting `notify_summary = true` also sends a summary after successful runs ("downloaded N new posts").

### Update checks

Decided 2026-09-27:

- About once a day, `run` asks GitHub for the repository's release tags. `check` always asks straight away.
- When a newer version exists, `run` prints the update command, and the line also goes to the log.
- If `notify_url` is set, PatronStash sends one notification per new version.
- It never updates itself.
- The check is on by default; `update_check = false` turns it off. The docs say it contacts `api.github.com`.
- If GitHub can't be reached, nothing is reported and the run carries on normally.
- *Why:* PatronStash depends on fixes for Patreon, Cloudflare and YouTube changes. Most users run it on a timer and never look for updates, so without this they'd miss them.

## 8. Scheduling

- PatronStash doesn't schedule itself. `patronstash run` does one pass and exits.
- The README shows how to schedule it with a **systemd user timer** (the recommended way, and what the maintainer uses) or with **cron**.

## 9. Development

- **Tests:** unit tests with `pytest`, written test-first, with `gallery-dl` mocked out. They cover:
  - config parsing and validation
  - backfill cutoffs
  - reserved keys
  - login-method precedence
  - the lock file
  - recording stats
  - `status` output
  - when notifications fire
- **CI:** GitHub Actions runs the tests on Python 3.11–3.14, plus `ruff`, on every push and pull request.
- **Live testing** needs real cookies and paid subscriptions, so it can't run in CI. It's done by hand, following a checklist in `CONTRIBUTING.md`, before each release.
- **Where it runs:**
  - development and testing happen on the maintainer's local machine
  - after that, it is deployed to an always-on Debian box (aarch64, Python 3.13, `ffmpeg` installed) through pipx and a systemd timer

## 10. Roadmap (not in v1)

- **Docker image:** runs on a schedule inside the container (default interval about 6 hours), supports `PUID`/`PGID` so files get the right owner on NAS boxes, and mounts `/config`, `/data` and `/downloads`. It's held back until someone who uses Docker can test and maintain it.
- **`rescan`:** re-checks old posts for files a creator replaced under the same ID.
- **Built-in browser login:** PatronStash opens a browser window, the user logs in, and the session is saved, with no need to export cookies.
- **Publishing to PyPI**, so users can run `pipx install patronstash`.
- Possibly: finding the creators you subscribe to automatically, with a way to exclude some, as an alternative to listing them all by hand.
