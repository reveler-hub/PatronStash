"""Getting past Patreon's Cloudflare bot check.

Since about August 2026 Cloudflare answers Patreon requests from plain
Python HTTP clients with 403, whatever the cookies and headers
(gallery-dl issue #9371). It recognises the client by its TLS/HTTP2
fingerprint. This adapter sends the requests for www.patreon.com through
curl_cffi instead, which has the same fingerprint as Chrome. Downloads
from Patreon's CDN are not challenged and keep using gallery-dl's normal
session.

The adapter also paces Patreon's API: by default a random 3-5 second wait
between API requests (`sleep-request` in the config). gallery-dl's own
`sleep-request` would also delay requests to Patreon's file server,
which don't need it, so PatronStash turns that off and paces here.
"""

from __future__ import annotations

import time
from http.cookiejar import CookieJar

import requests
from gallery_dl.util import build_duration_func
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

PATREON_PREFIX = "https://www.patreon.com/"
API_PREFIX = PATREON_PREFIX + "api/"
# The default wait between API requests; `sleep-request` in [gallery-dl]
# changes it, in any form gallery-dl accepts (3, [3, 5], "3-5").
DEFAULT_API_DELAY = (3.0, 5.0)
IMPERSONATE = "chrome"

# Headers that curl_cffi sets itself to match Chrome. gallery-dl's own
# User-Agent claims to be Patreon's Android app, which wouldn't match
# Chrome's fingerprint.
DROPPED_HEADERS = {"user-agent", "accept-encoding", "connection"}


class ApiPacer:
    """Waits between one API response and the next API request.

    Like gallery-dl's `sleep-request`, the wait is counted from when the
    previous response arrived, so a slow response doesn't eat into it.
    One pacer is shared by every adapter in the process, because each
    creator's run and the login check use their own gallery-dl extractor.
    """

    def __init__(self, delay=DEFAULT_API_DELAY, sleep=time.sleep, clock=time.monotonic):
        self.sleep = sleep
        self.clock = clock
        self.set_delay(delay)
        self._ready_at = None

    def set_delay(self, delay) -> None:
        """Seconds, a [min, max] range or a "min-max" string; ValueError if bad."""
        self._duration = build_duration_func(delay)  # None means no wait

    def wait(self) -> None:
        if self._ready_at is not None:
            remaining = self._ready_at - self.clock()
            if remaining > 0:
                self.sleep(remaining)

    def done(self) -> None:
        """A response arrived: the next request waits from now."""
        pause = self._duration() if self._duration is not None else 0.0
        self._ready_at = self.clock() + pause


API_PACER = ApiPacer()


def _curl_session():
    from curl_cffi import requests as curl_requests

    return curl_requests.Session(impersonate=IMPERSONATE)


def _timeout(timeout):
    if isinstance(timeout, tuple):
        values = [t for t in timeout if t]
        return max(values) if values else None
    return timeout


class ChromeAdapter(BaseAdapter):
    """A requests transport adapter that sends requests through curl_cffi.

    Cookies still come from the requests session, which puts them in the
    Cookie header. Cookies that responses set are copied back into `jar`.
    """

    def __init__(self, jar: CookieJar | None = None, session=None, pacer=None):
        super().__init__()
        self.jar = jar
        self.session = session if session is not None else _curl_session()
        self.pacer = pacer if pacer is not None else API_PACER

    def send(
        self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None
    ):
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in DROPPED_HEADERS
        }
        paced = request.url.startswith(API_PREFIX)
        if paced:
            self.pacer.wait()
        # The curl session must not add stale cookies of its own.
        self.session.cookies.clear()
        try:
            response = self.session.request(
                request.method,
                request.url,
                headers=headers,
                data=request.body,
                timeout=_timeout(timeout),
                verify=verify,
                allow_redirects=False,
                proxies=proxies or None,
            )
        except Exception as exc:
            if "timeout" in exc.__class__.__name__.lower():
                raise requests.exceptions.Timeout(exc, request=request) from exc
            raise requests.exceptions.ConnectionError(exc, request=request) from exc
        finally:
            if paced:
                self.pacer.done()

        if self.jar is not None:
            for cookie in self.session.cookies.jar:
                self.jar.set_cookie(cookie)

        result = requests.Response()
        result.status_code = response.status_code
        result.reason = response.reason
        result.headers = CaseInsensitiveDict(response.headers)
        result._content = response.content
        result._content_consumed = True
        result.url = request.url
        result.encoding = requests.utils.get_encoding_from_headers(result.headers)
        result.request = request
        result.connection = self
        return result

    def close(self):
        self.session.close()


def set_api_delay(delay) -> None:
    """Set the wait between API requests (see ApiPacer.set_delay)."""
    API_PACER.set_delay(delay)


def use_chrome_transport(extractor) -> None:
    """Route an initialised gallery-dl extractor's www.patreon.com requests
    through the Chrome adapter."""
    extractor.initialize()
    extractor.session.mount(PATREON_PREFIX, ChromeAdapter(jar=extractor.cookies))
