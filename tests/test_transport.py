from http.cookiejar import Cookie, CookieJar

import pytest
import requests

from patronstash.transport import (
    PATREON_PREFIX,
    ApiPacer,
    ChromeAdapter,
    set_api_delay,
)


def make_cookie(name, value, domain=".patreon.com"):
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        domain,
        True,
        True,
        "/",
        True,
        False,
        None,
        False,
        None,
        None,
        {},
    )


class RecordingPacer:
    def __init__(self):
        self.waits = 0
        self.dones = 0

    def wait(self):
        self.waits += 1

    def done(self):
        self.dones += 1


class FakeCurlCookies:
    def __init__(self):
        self.jar = CookieJar()

    def clear(self):
        self.jar.clear()


class FakeCurlResponse:
    def __init__(self, status=200, content=b'{"ok": true}', headers=None):
        self.status_code = status
        self.reason = "OK" if status == 200 else "Forbidden"
        self.content = content
        self.headers = headers or {"Content-Type": "application/json; charset=utf-8"}


class FakeCurlSession:
    def __init__(self, response=None, set_cookies=(), error=None):
        self.cookies = FakeCurlCookies()
        self.response = response or FakeCurlResponse()
        self.set_cookies = set_cookies
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        for cookie in self.set_cookies:
            self.cookies.jar.set_cookie(cookie)
        return self.response

    def close(self):
        pass


def session_with(fake, pacer=None):
    pacer = pacer or RecordingPacer()
    s = requests.Session()
    s.headers["User-Agent"] = "Patreon/126 (Android)"
    s.cookies.set("session_id", "secret", domain=".patreon.com")
    s.mount(PATREON_PREFIX, ChromeAdapter(jar=s.cookies, session=fake, pacer=pacer))
    return s


def test_patreon_requests_go_through_curl():
    fake = FakeCurlSession()
    s = session_with(fake)
    r = s.get("https://www.patreon.com/api/current_user", timeout=(5, 30))
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert r.encoding == "utf-8"
    method, url, kw = fake.calls[0]
    assert (method, url) == ("GET", "https://www.patreon.com/api/current_user")
    assert kw["headers"]["Cookie"] == "session_id=secret"
    assert "User-Agent" not in kw["headers"]  # curl_cffi sends Chrome's
    assert kw["timeout"] == 30
    assert kw["allow_redirects"] is False


def test_error_status_is_passed_through():
    fake = FakeCurlSession(FakeCurlResponse(403, b'{"errors": []}'))
    r = session_with(fake).get("https://www.patreon.com/x")
    assert r.status_code == 403
    assert r.reason == "Forbidden"


def test_response_cookies_reach_the_requests_jar():
    fake = FakeCurlSession(set_cookies=[make_cookie("__cf_bm", "fresh")])
    s = session_with(fake)
    s.get("https://www.patreon.com/x")
    assert s.cookies.get("__cf_bm") == "fresh"


def test_curl_session_cookies_are_cleared_before_each_request():
    fake = FakeCurlSession()
    fake.cookies.jar.set_cookie(make_cookie("stale", "1"))
    session_with(fake).get("https://www.patreon.com/x")
    assert not list(fake.cookies.jar)


def test_errors_become_requests_errors():
    class ReadTimeout(Exception):
        pass

    s = session_with(FakeCurlSession(error=ReadTimeout("slow")))
    with pytest.raises(requests.exceptions.Timeout):
        s.get("https://www.patreon.com/x")
    s = session_with(FakeCurlSession(error=OSError("refused")))
    with pytest.raises(requests.exceptions.ConnectionError):
        s.get("https://www.patreon.com/x")


def test_other_hosts_are_not_routed(monkeypatch):
    fake = FakeCurlSession()
    s = session_with(fake)
    adapter = s.get_adapter("https://c10.patreonusercontent.com/file.jpg")
    assert not isinstance(adapter, ChromeAdapter)
    assert isinstance(s.get_adapter("https://www.patreon.com/api/x"), ChromeAdapter)


# ── API pacing ─────────────────────────────────────────────────────


def test_only_api_requests_are_paced():
    pacer = RecordingPacer()
    s = session_with(FakeCurlSession(), pacer)
    s.get("https://www.patreon.com/api/posts")
    s.get("https://www.patreon.com/file?h=1&m=2")  # attachment redirect
    s.get("https://www.patreon.com/api/campaigns")
    assert (pacer.waits, pacer.dones) == (2, 2)


def test_failed_api_request_still_counts_for_pacing():
    pacer = RecordingPacer()
    s = session_with(FakeCurlSession(error=OSError("refused")), pacer)
    with pytest.raises(requests.exceptions.ConnectionError):
        s.get("https://www.patreon.com/api/posts")
    assert pacer.dones == 1


class FakeTime:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


def test_pacer_counts_from_the_end_of_the_previous_response():
    t = FakeTime()
    pacer = ApiPacer(3, sleep=t.sleep, clock=t.clock)
    pacer.wait()  # the first request doesn't wait
    t.now += 4.0  # a slow response
    pacer.done()
    t.now += 1.0
    pacer.wait()
    assert t.slept == [2.0]  # the full 3 s from the end, not from the start


def test_pacer_does_not_wait_when_enough_time_passed():
    t = FakeTime()
    pacer = ApiPacer((3.0, 5.0), sleep=t.sleep, clock=t.clock)
    pacer.done()
    t.now += 10
    pacer.wait()
    assert t.slept == []


@pytest.mark.parametrize(
    "delay, low, high",
    [(8, 8, 8), ([1, 2.5], 1, 2.5), ("3.0-5.0", 3, 5), ("2.5", 2.5, 2.5)],
)
def test_pacer_accepts_every_gallery_dl_delay_form(delay, low, high):
    t = FakeTime()
    pacer = ApiPacer(delay, sleep=t.sleep, clock=t.clock)
    for _ in range(20):
        pacer.done()
        pacer.wait()
    assert all(low <= s <= high for s in t.slept) and len(t.slept) == 20


def test_zero_delay_means_no_wait():
    t = FakeTime()
    pacer = ApiPacer(0, sleep=t.sleep, clock=t.clock)
    pacer.done()
    pacer.wait()
    assert t.slept == []


def test_bad_delay_is_rejected():
    with pytest.raises(ValueError):
        ApiPacer("fast")


def test_set_api_delay_changes_the_shared_pacer():
    from patronstash import transport

    saved = transport.API_PACER._duration
    try:
        set_api_delay("7")
        assert transport.API_PACER._duration() == 7.0
    finally:
        transport.API_PACER._duration = saved
