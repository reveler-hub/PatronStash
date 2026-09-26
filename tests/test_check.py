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


def check(tmp_path, text, login_ok=True, ffmpeg=True, notify_ok=True, deno=True):
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
        out=out.append,
    )
    return code, "\n".join(out), notifiers


def test_all_good(tmp_path):
    code, text, _ = check(tmp_path, GOOD)
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
