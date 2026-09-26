"""Telling users when a newer PatronStash has been released.

PatronStash asks GitHub for the repository's release tags (`v0.1.3`, …) at
most once a day and remembers the answer in data_dir. It only reports a new
version; updating stays the user's choice. `update_check = false` in the
config turns this off.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("patronstash")

REPO = "reveler-hub/PatronStash"
TAGS_URL = f"https://api.github.com/repos/{REPO}/tags?per_page=100"
CHECK_EVERY = timedelta(hours=24)
TIMEOUT = 5  # seconds; a slow GitHub must not hold up a run
STATE_FILE = "update-check.json"
UPDATE_COMMAND = 'pipx upgrade --pip-args="--upgrade-strategy eager" patronstash'

VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)*)")


def parse_version(text: str) -> tuple[int, ...] | None:
    """(0, 1, 3) for "v0.1.3" or "0.1.3"; None for anything else."""
    match = VERSION_RE.fullmatch(text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def fetch_tags() -> list[str]:
    """The names of the repository's tags on GitHub."""
    import requests

    from . import __version__

    response = requests.get(
        TAGS_URL,
        timeout=TIMEOUT,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"PatronStash/{__version__}",
        },
    )
    response.raise_for_status()
    return [tag["name"] for tag in response.json()]


def latest_version(fetch=None) -> str | None:
    """The highest release version among the tags, e.g. "0.1.4"."""
    versions = [v for v in map(parse_version, (fetch or fetch_tags)()) if v is not None]
    return ".".join(map(str, max(versions))) if versions else None


@dataclass
class UpdateInfo:
    current: str
    latest: str

    @property
    def available(self) -> bool:
        return parse_version(self.latest) > parse_version(self.current)

    def message(self) -> str:
        return (
            f"PatronStash {self.latest} is available (you have {self.current}). "
            f"Update with:\n  {UPDATE_COMMAND}"
        )


def _load_state(data_dir: Path) -> dict:
    try:
        state = json.loads((Path(data_dir) / STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(data_dir: Path, state: dict) -> None:
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / STATE_FILE).write_text(json.dumps(state))
    except OSError as exc:
        log.debug("could not save the update-check state: %s", exc)


def check_for_update(
    data_dir: Path,
    current: str,
    *,
    now: datetime,
    force: bool = False,
    fetch=None,
) -> UpdateInfo | None:
    """Compare the running version with the latest release.

    Asks GitHub at most once every CHECK_EVERY unless `force`. Returns None
    when the latest version isn't known (never checked, and GitHub can't be
    reached) or the running version has no number (a source checkout).
    """
    if parse_version(current) is None:
        return None
    state = _load_state(data_dir)
    try:
        checked_at = datetime.fromisoformat(state["checked_at"])
    except (KeyError, TypeError, ValueError):
        checked_at = None

    if force or checked_at is None or now - checked_at >= CHECK_EVERY:
        try:
            latest = latest_version(fetch)
        except Exception as exc:
            log.debug("update check failed: %s: %s", exc.__class__.__name__, exc)
        else:
            if latest is not None:
                state.update(checked_at=now.isoformat(), latest=latest)
                _save_state(data_dir, state)

    latest = state.get("latest")
    if not isinstance(latest, str) or parse_version(latest) is None:
        return None
    return UpdateInfo(current, latest)


def was_notified(data_dir: Path, version: str) -> bool:
    return _load_state(data_dir).get("notified") == version


def mark_notified(data_dir: Path, version: str) -> None:
    state = _load_state(data_dir)
    state["notified"] = version
    _save_state(data_dir, state)
