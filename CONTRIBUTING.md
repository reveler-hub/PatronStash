# Contributing

## Development setup

```sh
git clone https://github.com/reveler-hub/PatronStash
cd PatronStash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Tests and lint

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Tests are written test-first, with gallery-dl mocked out. `tests/test_gdl.py` drives a real gallery-dl job against a fake Patreon extractor that uses `text:` URLs, so the on-disk layout, metadata files and download archive are covered without network access.

CI runs the tests on Python 3.11–3.14 and runs `ruff` on every push and pull request.

## Code layout

| Module | Role |
|---|---|
| `config.py` | Loads and validates `config.toml`, normalises creator names, picks the login method |
| `gdl.py` | Everything that touches gallery-dl: its config, the post filter (locked posts, backfill cutoff, video, embeds, YouTube links), the download job, campaign lookup and the login check |
| `transport.py` | The curl_cffi adapter that gets `www.patreon.com` requests past Cloudflare, and paces API requests |
| `watch.py`, `tui.py` | `patronstash watch`: the pass loop and the state it shows (`watch.py`), and the curses dashboard (`tui.py`, whose `layout()` is testable without a terminal) |
| `updates.py` | The daily check for a newer release on GitHub |
| `runner.py` | `patronstash run`: lock, login check, one pass over the creators, notifications |
| `stats.py` | `stats.db`: one row per file, plus per-creator backfill state |
| `check.py`, `status.py` | The `check` and `status` commands |
| `lock.py`, `logs.py`, `notify.py`, `progress.py`, `fmt.py` | Lock file, logging, Apprise, `run`'s progress output, formatting |

## Releasing

1. Go through the live-test checklist below.
2. Bump `version` in `pyproject.toml`. That is the only place the version is set; `patronstash --version` reads it from there.
3. Commit, then tag the release (`git tag v0.1.1`) and push the tag.

Users install from git, so bumping the version is what makes `pipx upgrade` pick up a release. If the version stays the same, pipx keeps the old code even when there are new commits.

## Live-test checklist (before each release)

Live testing needs real cookies and paid subscriptions, so it can't run in CI. Before tagging a release, run through this list with a scratch `download_dir` and `data_dir`:

- [ ] `patronstash` with no arguments prints help and downloads nothing.
- [ ] With no config file, `patronstash check` creates the commented template.
- [ ] `patronstash check` passes with `cookies_file`, and the report names that method.
- [ ] `patronstash check` passes with `cookies_from_browser` (a Firefox-family browser, and a Chromium-family one if available).
- [ ] With both login methods set, `check` and the run log say `cookies_file` is used.
- [ ] Expired or garbled cookies: `run` downloads nothing, exits non-zero, logs the error and sends a notification.
- [ ] `backfill = "none"`: the first run downloads nothing old, and a post published afterwards is downloaded on the next run.
- [ ] `backfill = "<date>"`: nothing older than the date is downloaded.
- [ ] `backfill = "all"` on a small creator: the whole history is downloaded. Interrupt it with Ctrl-C partway, and check that the next run resumes it.
- [ ] Layout: `<vanity>/<date> <title> [<id>]/` holds the numbered images, attachments under their own names, `post.json` and `post.html`.
- [ ] A Patreon-hosted video downloads and plays. A YouTube or Vimeo embed downloads as `embed.<ext>`.
- [ ] An unlisted YouTube link in a post's text downloads as `youtube-<id>.<ext>`. A public one is logged as left alone and isn't asked about again on the next run.
- [ ] With `deno` on `PATH`, `check` says so, and yt-dlp prints no "challenge solving failed" warning.
- [ ] A creator name that doesn't exist fails that creator with "no Patreon creator named …", and the others still run.
- [ ] With `ffmpeg` removed from `PATH`, video is skipped with a single warning and everything else downloads.
- [ ] A locked post (a tier you don't pay for) creates no folder, and the summary line counts it.
- [ ] A second `run` straight afterwards downloads nothing and finishes quickly.
- [ ] Starting a second `run` while one is going prints "already running" and exits.
- [ ] A creator without `backfill` is skipped with a warning and a notification, and the others still run.
- [ ] `[gallery-dl] archive = "..."` is ignored with a warning, and `check` flags it.
- [ ] `patronstash status` shows correct counts, sizes and last-run results.
- [ ] `notify_summary = true` sends a summary after a run that downloaded something.
- [ ] The systemd timer from the README runs the job.
