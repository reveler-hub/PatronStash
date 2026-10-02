from patronstash.check import run_check
from patronstash.gdl import LoginResult

GOOD = """
download_dir = "{dl}"
cookies_file = "/c.txt"

[[creator]]
name = "a"
backfill = "all"

[[creator]]
name = "b"
backfill = "none"
"""


class FakeNotifier:
    def __init__(self, url, ok=True):
        self.url = url
        self.ok = ok
        self.sent = []

    @property
    def enabled(self):
        return bool(self.url)

    def send(self, title, body):
        self.sent.append(title)
        return self.ok


def check(
    tmp_path, text, login_ok=True, ffmpeg=True, notify_ok=True, deno=True, tags=None
):
    path = tmp_path / "config.toml"
    path.write_text(text.replace("{dl}", str(tmp_path)))
    out = []
    notifiers = []

    def make_notifier(url):
        n = FakeNotifier(url, notify_ok)
        notifiers.append(n)
        return n

    code = run_check(
        path,
        login_checker=lambda cfg: LoginResult(
            login_ok, "logged in as T via cookies_file" if login_ok else "expired"
        ),
        ffmpeg_available=lambda: ffmpeg,
        deno_available=lambda: deno,
        make_notifier=make_notifier,
        fetch_tags=(lambda: tags) if tags is not None else None,
        version="0.1.3",
        out=out.append,
    )
    return code, "\n".join(out), notifiers


def test_all_good(tmp_path):
    code, text, _ = check(tmp_path, GOOD, tags=["v0.1.3"])
    assert code == 0
    assert "❌" not in text and "⚠" not in text
    assert "2 creators" in text
    assert "logged in as T via cookies_file" in text


def test_bad_toml(tmp_path):
    code, text, _ = check(tmp_path, "download_dir = \n")
    assert code == 1
    assert "❌" in text and "TOML" in text


def test_missing_backfill_warns(tmp_path):
    code, text, _ = check(tmp_path, GOOD + '[[creator]]\nname = "forgot"\n')
    assert code == 0
    assert "⚠" in text and "forgot" in text


def test_reserved_key_warns(tmp_path):
    code, text, _ = check(tmp_path, GOOD + '[gallery-dl]\narchive = "x"\n')
    assert code == 0
    assert "⚠" in text and "gallery-dl.archive" in text


def test_login_failure(tmp_path):
    code, text, _ = check(tmp_path, GOOD, login_ok=False)
    assert code == 1
    assert "❌" in text and "expired" in text


def test_missing_ffmpeg_warns(tmp_path):
    code, text, _ = check(tmp_path, GOOD, ffmpeg=False)
    assert code == 0
    assert "⚠" in text and "ffmpeg" in text


def test_notification_is_tested_when_configured(tmp_path):
    code, text, notifiers = check(tmp_path, 'notify_url = "ntfy://x"\n' + GOOD)
    assert code == 0
    assert notifiers[0].sent
    assert "test notification" in text.lower()


def test_notification_failure(tmp_path):
    code, text, _ = check(tmp_path, 'notify_url = "ntfy://x"\n' + GOOD, notify_ok=False)
    assert code == 1
    assert "❌" in text


def test_no_notification_when_not_configured(tmp_path):
    _, _, notifiers = check(tmp_path, GOOD)
    assert not any(n.sent for n in notifiers)


def test_missing_deno_warns(tmp_path):
    code, text, _ = check(tmp_path, GOOD, deno=False)
    assert code == 0
    assert "⚠" in text and "deno" in text


def test_check_reports_a_new_version(tmp_path):
    code, text, _ = check(tmp_path, GOOD, tags=["v0.1.4"])
    assert code == 0
    assert "⚠ PatronStash 0.1.4 is available (you have 0.1.3)" in text
    assert "pipx upgrade" in text


def test_check_reports_up_to_date(tmp_path):
    _, text, _ = check(tmp_path, GOOD, tags=["v0.1.3"])
    assert "✅ PatronStash 0.1.3 is the latest version" in text


def test_check_when_github_is_unreachable(tmp_path):
    code, text, _ = check(tmp_path, GOOD)
    assert code == 0
    assert "⚠ Update check: couldn't reach GitHub" in text


def test_check_with_update_check_off(tmp_path):
    _, text, _ = check(tmp_path, "update_check = false\n" + GOOD, tags=["v9.9.9"])
    assert "✅ Update check: turned off" in text


def test_youtube_cookies_file_reported(tmp_path):
    (tmp_path / "yt.txt").write_text("# Netscape HTTP Cookie File\n")
    _, text, _ = check(tmp_path, f'youtube_cookies_file = "{tmp_path}/yt.txt"\n' + GOOD)
    assert "✅ YouTube cookies:" in text


def test_missing_youtube_cookies_file_fails_check(tmp_path):
    code, text, _ = check(tmp_path, 'youtube_cookies_file = "/nope/yt.txt"\n' + GOOD)
    assert code == 1
    assert "❌ YouTube cookies file not found" in text
