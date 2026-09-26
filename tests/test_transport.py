from http.cookiejar import Cookie, CookieJar

import pytest
import requests

from patronstash.transport import PATREON_PREFIX, ChromeAdapter


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


def session_with(fake):
    s = requests.Session()
    s.headers["User-Agent"] = "Patreon/126 (Android)"
    s.cookies.set("session_id", "secret", domain=".patreon.com")
    s.mount(PATREON_PREFIX, ChromeAdapter(jar=s.cookies, session=fake))
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
