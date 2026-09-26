"""Getting past Patreon's Cloudflare bot check.

Since about August 2026 Cloudflare answers Patreon requests from plain
Python HTTP clients with 403, whatever the cookies and headers
(gallery-dl issue #9371). It recognises the client by its TLS/HTTP2
fingerprint. This adapter sends the requests for www.patreon.com through
curl_cffi instead, which has the same fingerprint as Chrome. Downloads
from Patreon's CDN are not challenged and keep using gallery-dl's normal
session.
"""

from __future__ import annotations

from http.cookiejar import CookieJar

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

PATREON_PREFIX = "https://www.patreon.com/"
IMPERSONATE = "chrome"

# Headers that curl_cffi sets itself to match Chrome. gallery-dl's own
# User-Agent claims to be Patreon's Android app, which wouldn't match
# Chrome's fingerprint.
DROPPED_HEADERS = {"user-agent", "accept-encoding", "connection"}


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

    def __init__(self, jar: CookieJar | None = None, session=None):
        super().__init__()
        self.jar = jar
        self.session = session if session is not None else _curl_session()

    def send(
        self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None
    ):
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in DROPPED_HEADERS
        }
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


def use_chrome_transport(extractor) -> None:
    """Route an initialised gallery-dl extractor's www.patreon.com requests
    through the Chrome adapter."""
    extractor.initialize()
    extractor.session.mount(PATREON_PREFIX, ChromeAdapter(jar=extractor.cookies))
