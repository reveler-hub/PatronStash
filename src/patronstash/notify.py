"""Optional notifications through Apprise."""

from __future__ import annotations

import logging

log = logging.getLogger("patronstash")


class Notifier:
    """Sends notifications to one Apprise URL; does nothing without one."""

    def __init__(self, url: str | None):
        self.url = url

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def send(self, title: str, body: str) -> bool:
        if not self.url:
            return False
        import apprise

        client = apprise.Apprise()
        if not client.add(self.url):
            log.error("notify_url is not a valid Apprise URL")
            return False
        try:
            ok = bool(client.notify(title=title, body=body))
        except Exception as exc:
            log.error("notification failed: %s: %s", exc.__class__.__name__, exc)
            return False
        if not ok:
            log.error("notification could not be delivered to notify_url")
        return ok
