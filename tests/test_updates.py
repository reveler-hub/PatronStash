from datetime import datetime, timedelta

import pytest

from patronstash.updates import (
    UPDATE_COMMAND,
    check_for_update,
    latest_version,
    mark_notified,
    parse_version,
    was_notified,
)

NOW = datetime(2026, 9, 27, 12, 0)


class FakeFetch:
    def __init__(self, tags=("v0.1.4", "v0.1.3"), error=None):
        self.tags = list(tags)
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.tags


def test_parse_version():
    assert parse_version("v0.1.3") == (0, 1, 3)
    assert parse_version("0.10.0") == (0, 10, 0)
    assert parse_version("v1.2.3-beta") is None
    assert parse_version("unknown") is None


def test_latest_version_ignores_non_release_tags():
    fetch = FakeFetch(["v0.1.9", "v0.1.10", "nightly", "v0.2.0rc1"])
    assert latest_version(fetch) == "0.1.10"  # compared as numbers, not text


def test_newer_version_is_reported(tmp_path):
    info = check_for_update(tmp_path, "0.1.3", now=NOW, fetch=FakeFetch())
    assert info.available
    assert (info.current, info.latest) == ("0.1.3", "0.1.4")


def test_same_or_older_is_not_reported(tmp_path):
    assert not check_for_update(tmp_path, "0.1.4", now=NOW, fetch=FakeFetch()).available
    # a development copy ahead of the latest release
    assert not check_for_update(tmp_path, "0.2.0", now=NOW, fetch=FakeFetch()).available


def test_checks_github_at_most_once_a_day(tmp_path):
    fetch = FakeFetch()
    check_for_update(tmp_path, "0.1.3", now=NOW, fetch=fetch)
    info = check_for_update(
        tmp_path, "0.1.3", now=NOW + timedelta(hours=23), fetch=fetch
    )
    assert fetch.calls == 1
    assert info.latest == "0.1.4"  # remembered from the first check
    check_for_update(tmp_path, "0.1.3", now=NOW + timedelta(hours=25), fetch=fetch)
    assert fetch.calls == 2


def test_force_ignores_the_daily_limit(tmp_path):
    fetch = FakeFetch()
    check_for_update(tmp_path, "0.1.3", now=NOW, fetch=fetch)
    check_for_update(tmp_path, "0.1.3", now=NOW, fetch=fetch, force=True)
    assert fetch.calls == 2


def test_github_unreachable_uses_last_known_result(tmp_path):
    check_for_update(tmp_path, "0.1.3", now=NOW, fetch=FakeFetch())
    later = NOW + timedelta(days=2)
    info = check_for_update(
        tmp_path, "0.1.3", now=later, fetch=FakeFetch(error=OSError())
    )
    assert info.latest == "0.1.4"


def test_github_unreachable_and_nothing_known(tmp_path):
    info = check_for_update(
        tmp_path, "0.1.3", now=NOW, fetch=FakeFetch(error=OSError())
    )
    assert info is None


def test_unknown_current_version_is_not_checked(tmp_path):
    fetch = FakeFetch()
    assert check_for_update(tmp_path, "unknown", now=NOW, fetch=fetch) is None
    assert fetch.calls == 0


def test_corrupt_state_file_is_ignored(tmp_path):
    (tmp_path / "update-check.json").write_text("{not json")
    info = check_for_update(tmp_path, "0.1.3", now=NOW, fetch=FakeFetch())
    assert info.latest == "0.1.4"


def test_notified_once_per_version(tmp_path):
    check_for_update(tmp_path, "0.1.3", now=NOW, fetch=FakeFetch())
    assert not was_notified(tmp_path, "0.1.4")
    mark_notified(tmp_path, "0.1.4")
    assert was_notified(tmp_path, "0.1.4")
    assert not was_notified(tmp_path, "0.1.5")
    # remembering the notification keeps the cached check result
    fetch = FakeFetch()
    assert check_for_update(tmp_path, "0.1.3", now=NOW, fetch=fetch).latest == "0.1.4"
    assert fetch.calls == 0


@pytest.mark.parametrize("current", ["0.1.3"])
def test_message_includes_the_update_command(tmp_path, current):
    info = check_for_update(tmp_path, current, now=NOW, fetch=FakeFetch())
    assert info.message() == (
        f"PatronStash 0.1.4 is available (you have 0.1.3). Update with:\n"
        f"  {UPDATE_COMMAND}"
    )
