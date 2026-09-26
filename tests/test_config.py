from datetime import date
from pathlib import Path

import pytest

from patronstash.config import (
    Backfill,
    ConfigError,
    load_config,
    validate_creator_name,
    write_template,
)


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


BASE = 'download_dir = "/archive"\ncookies_file = "/c/cookies.txt"\n'


# ── creator names ──────────────────────────────────────────────────


def test_vanity_names_are_accepted():
    assert validate_creator_name("somecreator") == "somecreator"
    assert validate_creator_name("  somecreator  ") == "somecreator"
    assert validate_creator_name("Some_Creator-2") == "Some_Creator-2"


@pytest.mark.parametrize(
    "value",
    [
        "https://www.patreon.com/somecreator",
        "https://patreon.com/somecreator/",
        "patreon.com/somecreator",
        "www.patreon.com/somecreator/posts",
        "https://www.patreon.com/c/somecreator",
        "https://www.patreon.com/cw/somecreator/posts?filters[tag]=x",
        "http://www.patreon.com/somecreator#top",
    ],
)
def test_urls_are_rejected_with_the_vanity_name_to_use(value):
    with pytest.raises(ValueError, match='use the vanity name "somecreator"'):
        validate_creator_name(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://www.patreon.com/",
        "https://www.patreon.com/posts/some-post-123",
        "https://www.patreon.com/home",
        "https://example.com/somecreator",
        "some creator",
    ],
)
def test_bad_names_are_rejected(value):
    with pytest.raises(ValueError):
        validate_creator_name(value)


# ── backfill ───────────────────────────────────────────────────────


def test_backfill_parse():
    assert Backfill.parse("all") == Backfill("all")
    assert Backfill.parse("none") == Backfill("none")
    assert Backfill.parse("2024-01-01") == Backfill("since", date(2024, 1, 1))
    assert str(Backfill.parse("2024-01-01")) == "2024-01-01"
    assert str(Backfill.parse("all")) == "all"


def test_backfill_accepts_toml_date():
    assert Backfill.parse(date(2024, 5, 6)) == Backfill("since", date(2024, 5, 6))


@pytest.mark.parametrize("value", ["some", "2024-13-01", "", 5, "ALL "])
def test_backfill_rejects_bad_values(value):
    with pytest.raises(ValueError):
        Backfill.parse(value)


# ── loading ────────────────────────────────────────────────────────


def test_minimal_config(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            BASE
            + """
[[creator]]
name = "somecreator"
backfill = "all"

[[creator]]
name = "othercreator"
backfill = 2024-01-01
""",
        )
    )
    assert cfg.download_dir == Path("/archive")
    assert [c.name for c in cfg.creators] == ["somecreator", "othercreator"]
    assert cfg.creators[1].backfill == Backfill("since", date(2024, 1, 1))
    assert cfg.creators[0].url == "https://www.patreon.com/somecreator"
    assert cfg.missing_backfill == []
    assert cfg.warnings == []
    assert cfg.notify_url is None
    assert cfg.notify_summary is False


def test_tilde_paths_are_expanded(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            'download_dir = "~/dl"\ndata_dir = "~/data"\n'
            'cookies_file = "~/cookies.txt"\n',
        )
    )
    assert cfg.download_dir == Path.home() / "dl"
    assert cfg.data_dir == Path.home() / "data"
    assert cfg.login.cookies_file == Path.home() / "cookies.txt"


def test_default_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    cfg = load_config(write(tmp_path, BASE))
    assert cfg.data_dir == Path.home() / ".local/share/patronstash"


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_bad_toml(tmp_path):
    with pytest.raises(ConfigError, match="TOML"):
        load_config(write(tmp_path, "download_dir = \n"))


def test_download_dir_required(tmp_path):
    with pytest.raises(ConfigError, match="download_dir"):
        load_config(write(tmp_path, 'cookies_file = "x"\n'))


def test_creator_missing_backfill_is_skipped(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            BASE
            + """
[[creator]]
name = "nobackfill"

[[creator]]
name = "ok"
backfill = "none"
""",
        )
    )
    assert [c.name for c in cfg.creators] == ["ok"]
    assert cfg.missing_backfill == ["nobackfill"]


def test_invalid_backfill_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="backfill"):
        load_config(write(tmp_path, BASE + '[[creator]]\nname = "a"\nbackfill = "x"\n'))


def test_creator_without_name_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="name"):
        load_config(write(tmp_path, BASE + '[[creator]]\nbackfill = "all"\n'))


def test_duplicate_creator_warns_and_keeps_first(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            BASE
            + """
[[creator]]
name = "dup"
backfill = "all"

[[creator]]
name = "DUP"
backfill = "none"
""",
        )
    )
    assert len(cfg.creators) == 1
    assert cfg.creators[0].backfill == Backfill("all")
    assert any("dup" in w.lower() for w in cfg.warnings)


def test_unknown_keys_warn(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            BASE + 'downlod_dir = "typo"\n[[creator]]\nname = "a"\nbackfill = "all"\n'
            "tier = 3\n",
        )
    )
    assert any("downlod_dir" in w for w in cfg.warnings)
    assert any("tier" in w for w in cfg.warnings)


def test_notify_options(tmp_path):
    cfg = load_config(
        write(
            tmp_path, BASE + 'notify_url = "ntfy://ntfy.sh/t"\nnotify_summary = true\n'
        )
    )
    assert cfg.notify_url == "ntfy://ntfy.sh/t"
    assert cfg.notify_summary is True


def test_wrong_type_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="notify_summary"):
        load_config(write(tmp_path, BASE + 'notify_summary = "yes"\n'))


# ── login precedence ───────────────────────────────────────────────


def test_login_cookies_file(tmp_path):
    cfg = load_config(write(tmp_path, 'download_dir = "/a"\ncookies_file = "/c.txt"\n'))
    assert cfg.login.method == "cookies_file"
    assert cfg.login.gallery_dl_cookies() == "/c.txt"
    assert "cookies_file" in cfg.login.describe()


def test_login_browser(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            'download_dir = "/a"\ncookies_from_browser = "Firefox"\n'
            'browser_profile = "default-release"\n',
        )
    )
    assert cfg.login.method == "cookies_from_browser"
    assert cfg.login.gallery_dl_cookies() == ["firefox", "default-release"]
    assert "firefox" in cfg.login.describe()


def test_login_browser_without_profile(tmp_path):
    cfg = load_config(
        write(tmp_path, 'download_dir = "/a"\ncookies_from_browser = "zen"\n')
    )
    assert cfg.login.gallery_dl_cookies() == ["zen"]


def test_cookies_file_wins_over_browser(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            'download_dir = "/a"\ncookies_file = "/c.txt"\n'
            'cookies_from_browser = "firefox"\n',
        )
    )
    assert cfg.login.method == "cookies_file"
    assert any("cookies_from_browser" in w for w in cfg.warnings)


def test_no_login(tmp_path):
    cfg = load_config(write(tmp_path, 'download_dir = "/a"\n'))
    assert cfg.login is None


def test_unsupported_browser(tmp_path):
    with pytest.raises(ConfigError, match="netscape"):
        load_config(
            write(tmp_path, 'download_dir = "/a"\ncookies_from_browser = "netscape"\n')
        )


# ── gallery-dl passthrough & reserved keys ─────────────────────────


def test_passthrough_is_kept(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            BASE
            + """
[gallery-dl]
sleep-request = [1.0, 2.0]
directory = ["{id}"]
""",
        )
    )
    assert cfg.passthrough == {"sleep-request": [1.0, 2.0], "directory": ["{id}"]}
    assert cfg.reserved_keys == []


def test_reserved_keys_are_removed_and_reported(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            BASE
            + """
[gallery-dl]
archive = "/elsewhere.db"
sleep = 1

[gallery-dl.patreon]
cookies = "/other.txt"
""",
        )
    )
    assert cfg.passthrough == {"sleep": 1, "patreon": {}}
    assert sorted(cfg.reserved_keys) == [
        "gallery-dl.archive",
        "gallery-dl.patreon.cookies",
    ]
    assert any("archive" in w for w in cfg.warnings)


# ── template ───────────────────────────────────────────────────────


def test_template_is_written_and_explains_backfill(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    write_template(path)
    text = path.read_text()
    assert "backfill" in text and "⚠" in text
    # download_dir is deliberately left unset, so loading must fail on it
    with pytest.raises(ConfigError, match="download_dir"):
        load_config(path)


def test_template_is_not_overwritten(tmp_path):
    path = write(tmp_path, BASE)
    with pytest.raises(FileExistsError):
        write_template(path)
    assert path.read_text() == BASE


def test_url_as_creator_name_is_a_config_error(tmp_path):
    text = (
        BASE
        + '[[creator]]\nname = "https://www.patreon.com/c/artist"\nbackfill = "all"\n'
    )
    with pytest.raises(ConfigError, match='creator #1: use the vanity name "artist"'):
        load_config(write(tmp_path, text))
