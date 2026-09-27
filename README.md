# PatronStash

PatronStash automatically archives content from the Patreon creators you support: posts, images, videos, attachments and the post text. Run it on a schedule and it downloads only what's new each time, so your paid content survives a lapsed subscription or a deleted post.

It's a thin wrapper around [gallery-dl](https://github.com/mikf/gallery-dl) (with [yt-dlp](https://github.com/yt-dlp/yt-dlp) for video). gallery-dl talks to Patreon. PatronStash adds a simple config file, safety checks, stats, logging and notifications.

Patreon's Cloudflare protection currently blocks gallery-dl on its own ([gallery-dl #9371](https://github.com/mikf/gallery-dl/issues/9371)). PatronStash gets past it by sending gallery-dl's Patreon requests through [curl_cffi](https://github.com/lexiforest/curl_cffi), which looks like Chrome to Cloudflare.

> **Unofficial.** PatronStash is not affiliated with or endorsed by Patreon. Only archive content you have paid for, for your own use.

## Requirements

- Linux or another Unix-like system
- Python 3.11 or newer
- `ffmpeg` for video. Without it, video is skipped and everything else still downloads.
- Optional: [`deno`](https://deno.com/) for full-quality YouTube video. Without it, YouTube may only offer lower-quality formats.
- A Patreon account that is logged in, in a browser

## Install

With [pipx](https://pipx.pypa.io/) (recommended):

```sh
pipx install git+https://github.com/reveler-hub/PatronStash
```

Or with pip, inside a virtual environment:

```sh
python3 -m venv ~/.venvs/patronstash
~/.venvs/patronstash/bin/pip install git+https://github.com/reveler-hub/PatronStash
```

### Update

```sh
pipx upgrade --pip-args="--upgrade-strategy eager" patronstash
```

PatronStash tells you when a new version is out. About once a day it asks GitHub for the latest release, and `run` then prints a line like this:

```
PatronStash 0.1.4 is available (you have 0.1.3). Update with:
  pipx upgrade --pip-args="--upgrade-strategy eager" patronstash
```

If notifications are set up, you also get one notification per new version. `patronstash check` always looks straight away. PatronStash never updates itself. The check contacts `api.github.com`, which sees your IP address; to turn it off, add `update_check = false` to the config.

The upgrade command installs the latest PatronStash release and also updates gallery-dl, yt-dlp and the other packages it uses. Those updates matter on their own: they're how fixes arrive when Patreon or YouTube change something. Plain `pipx upgrade patronstash` updates only PatronStash itself.

If you installed with pip into a virtual environment:

```sh
~/.venvs/patronstash/bin/pip install --upgrade --upgrade-strategy eager git+https://github.com/reveler-hub/PatronStash
```

## Set up

**1. Create the config file.** Run `patronstash check` once. On the first run it creates `~/.config/patronstash/config.toml` with commented examples and then stops.

**2. Choose where downloads go.** `download_dir` is required and has no default. A single backfill can be hundreds of GB, so point it at a disk with room to spare:

```toml
download_dir = "/mnt/archive/patreon"
```

**3. Log in.** PatronStash uses your browser's Patreon login cookies. There are two ways to give them to it:

- **`cookies_file`** works everywhere, including headless servers. Install a browser extension that exports cookies in the `cookies.txt` format, such as *Get cookies.txt LOCALLY*. Log in to patreon.com, export the cookies for that site, and save the file:

  ```toml
  cookies_file = "~/.config/patronstash/cookies.txt"
  ```

  Treat this file like a password: anyone who has it can use your Patreon account.

- **`cookies_from_browser`** reads the cookies straight from a browser on the same machine, so it only works on a desktop. It supports Firefox, LibreWolf, Zen, Floorp, Chrome, Chromium, Brave, Edge, Opera, Vivaldi and Thorium. On Linux, Chromium-based browsers need a working system keyring (GNOME Keyring or KWallet); Firefox-family browsers need nothing extra.

  ```toml
  cookies_from_browser = "chrome"
  browser_profile = "Profile 1"   # optional
  ```

  `browser_profile` picks which browser profile to read, which matters if you have more than one. For Chrome-family browsers it's a name like `Default` or `Profile 1`: open `chrome://version` in the profile that's logged in to Patreon and use the last part of **Profile Path**. For Firefox-family browsers it's usually `default-release`. Leave it out to use the default profile.

If you set both, `cookies_file` wins.

**4. Add creators.** Add one `[[creator]]` block per creator. `name` is the creator's **vanity name**: the part after `patreon.com/` in their page URL, so `somecreator` for `patreon.com/somecreator`. Full URLs aren't accepted; if you paste one, `check` tells you the vanity name to use instead. **Every creator needs a `backfill` line**, which decides how far back to go:

```toml
[[creator]]
name = "somecreator"
backfill = "none"          # only posts published from now on

[[creator]]
name = "othercreator"
backfill = "2024-01-01"    # everything since this date

[[creator]]
name = "thirdcreator"
backfill = "all"           # their entire post history
```

⚠ `"all"` can mean thousands of posts and tens of GB, especially for video. The first sync can take hours or days and span several runs; each run picks up where the last one stopped. PatronStash waits 3–5 seconds between requests to Patreon, but very large backfills may still get throttled.

A creator without a `backfill` line is skipped with a warning, and every other creator still runs.

**5. Check the setup:**

```sh
patronstash check
```

It downloads nothing. It prints a ✅ / ⚠ / ❌ line for each of these: the config file, backfill lines, the login (and which login method is in use), ffmpeg, deno, reserved passthrough keys and, if configured, a test notification.

## Use

| Command | What it does |
|---|---|
| `patronstash` | Shows help. It never starts a download by accident. |
| `patronstash run` | Does one pass over every creator and exits. Schedule this. |
| `patronstash check` | Validates the setup without downloading anything. |
| `patronstash status` | Shows one line per creator: backfill progress, posts, files, size on disk, the date of the newest post and the last run. |
| `patronstash watch` | Keeps running: does a pass now and then every few hours, with a live dashboard. See [Watch mode](#watch-mode). |

Global options, accepted before or after the command:

- `--config PATH` uses a different config file.
- `-v` shows gallery-dl's full per-file output.

While `run` works, it shows a line as it starts each creator, one line per post with new files, and the summary. In a terminal, big downloads such as videos also get a live progress bar:

```
login: logged in as YourName via cookies_from_browser (chrome, profile Default)
somecreator: checking for new posts…
  youtube-AbCdEfGhIjK.mkv  [######----]  61%  331.0 MB / 541.0 MB  4.2 MB/s
somecreator: 2026-09-25 Photo set — 6 files
```

During a long backfill it also reports every 50 posts checked, so you can tell it's still working.

### What gets downloaded

```
<download_dir>/<vanity-name>/<YYYY-MM-DD> <post title> [<post id>]/
    01.jpg  02.jpg  03.mp4  attachment.zip
    post.json    ← full metadata (tags, tier, date, URL)
    post.html    ← the post's text
```

- Images, Patreon-hosted video, attachments and the post text are downloaded.
- External video embeds (YouTube, Vimeo, SoundCloud…) are downloaded with yt-dlp as `embed.<ext>`.
- **YouTube links in the post text** are downloaded as `youtube-<id>.<ext>`, but only if the video is **unlisted or private**, which is how creators usually share patron-only videos. Public videos are left alone.
- Video is saved as **H.264** when available, because it plays on any device. yt-dlp's own default prefers AV1, which is about half the size but needs fairly recent hardware to play smoothly. To get AV1 instead, set `format = "bv*+ba/b"` under `[gallery-dl.downloader.ytdl]`.
- If an embed or link can't be downloaded, for example because the video was removed, PatronStash logs a warning, carries on, and tries again on the next run.
- **Locked posts** (tiers you don't pay for) are skipped: no folder is created, and the log counts them. If you upgrade your tier later, the next run downloads them.
- Folders are named after the creator's vanity name, which doesn't change when they rename themselves.

PatronStash remembers what it has downloaded in a database in its data directory. Files are never downloaded twice, even if you move or rename them.

Once a creator's backfill is done, later runs stop when they reach posts that are already archived. So if a creator adds an attachment to an *old* post, it won't be picked up. The same goes for a file that a creator replaces under the same ID.

## Watch mode

`patronstash watch` is the alternative to a timer: leave it running in a terminal, tmux or screen session, and it does a pass straight away and then one every 6 hours, showing everything on a full-screen dashboard:

<img width="890" height="920" alt="TUI ScreenShot" src="https://github.com/user-attachments/assets/b228ecc9-8b79-448a-a4a6-e908a9986686" />



- **Keys:** `r` starts a pass now; ↑/↓, PgUp/PgDn and Home/End scroll; `q` quits.
- **Interval:** `--every HOURS` changes it, e.g. `patronstash watch --every 3`.
- **Config changes** are picked up at the next pass, with no restart needed.
- **Output:** the log still goes to `patronstash.log`. Anything the download tools print goes to `watch-output.log` in the data folder, so it never draws over the dashboard.

Use either `watch` or a timer, not both. If a timer's run starts while `watch` is in a pass, it sees "already running" and skips.

## Schedule it

`patronstash run` does one pass and exits. Runs never overlap: if one starts while another is still going (say, a long backfill), it logs "already running" and exits.

### systemd user timer (recommended)

`~/.config/systemd/user/patronstash.service`:

```ini
[Unit]
Description=PatronStash Patreon archive
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/patronstash run
```

`~/.config/systemd/user/patronstash.timer`:

```ini
[Unit]
Description=Run PatronStash every 6 hours

[Timer]
OnBootSec=10min
OnUnitActiveSec=6h
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
```

Then:

If `deno` lives outside the standard `PATH` (for example `~/.deno/bin`), add it to the service so YouTube video downloads in full quality:

```ini
[Service]
Environment=PATH=%h/.deno/bin:/usr/local/bin:/usr/bin:/bin
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now patronstash.timer
# on a headless server, let user timers run while you're logged out:
sudo loginctl enable-linger "$USER"
```

To see what happened, run `journalctl --user -u patronstash` or `patronstash status`.

### cron

```cron
15 */6 * * * $HOME/.local/bin/patronstash run
```

Cron throws output away unless mail is set up, so check the log file (below) or set up notifications.

## Notifications

Notifications are optional. They go to any [Apprise](https://github.com/caronc/apprise/wiki) URL: ntfy, Discord, Telegram, email and more.

```toml
notify_url = "ntfy://ntfy.sh/my-secret-topic"
notify_summary = false   # true = also notify after runs that downloaded something
```

By default you're notified only when something needs attention:

- the login expired
- a creator has no `backfill` line
- a creator's run failed

## Files

| What | Where |
|---|---|
| Config | `~/.config/patronstash/config.toml` |
| Download archive, `stats.db`, log, lock file | `~/.local/share/patronstash/` (change with `data_dir`) |
| Log | `~/.local/share/patronstash/patronstash.log` (rotates at 5 × 5 MB) |

## Advanced: gallery-dl options

The `[gallery-dl]` table is passed straight to gallery-dl and overrides PatronStash's defaults. Its keys are [gallery-dl extractor options](https://gdl-org.github.io/docs/configuration.html). The `downloader`, `output`, `cache` and `postprocessor` sub-tables go to the matching top-level gallery-dl sections instead.

```toml
[gallery-dl]
sleep-request = [5.0, 8.0]                      # slower API requests
directory = ["{vanity}", "{date:%Y}", "{id}"]   # a different layout

[gallery-dl.downloader.ytdl]
format = "bestvideo[height<=1080]+bestaudio/best"
```

Setting `directory`, `filename` or `postprocessors` replaces PatronStash's layout. Every post has a `{vanity}` field holding the creator's vanity name.

`archive` and `cookies` are reserved because PatronStash depends on them. If you set them here they're ignored with a warning, and `check` flags them. To move the data files, use `data_dir`.

## Troubleshooting

- **"login failed … has expired"**: log in to patreon.com in your browser again. If you use `cookies_file`, export the cookies again.
- **Chrome/Brave cookies fail on Linux**: the keyring may be locked or missing. Use `cookies_file` instead.
- **Video is skipped**: install `ffmpeg`, then run `patronstash check`.
- **"Cloudflare challenge" or 403 errors**: Patreon's bot protection changed again. Update PatronStash and the packages it uses (see [Update](#update)), and check the gallery-dl issue tracker.
- **"no Patreon creator named …"**: use the name from the creator's page URL (`patreon.com/<name>`), not their display name.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).
