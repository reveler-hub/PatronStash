"""Loading and validating the PatronStash config file."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

# gallery-dl keys PatronStash depends on; the passthrough may not set them.
RESERVED_KEYS = ("archive", "cookies")

BROWSERS = (
    "firefox",
    "librewolf",
    "zen",
    "floorp",
    "chrome",
    "chromium",
    "brave",
    "edge",
    "opera",
    "vivaldi",
    "thorium",
)

TOP_LEVEL_KEYS = {
    "download_dir",
    "data_dir",
    "cookies_file",
    "cookies_from_browser",
    "browser_profile",
    "notify_url",
    "notify_summary",
    "creator",
    "gallery-dl",
}
CREATOR_KEYS = {"name", "backfill"}

# First path segments on patreon.com that are pages, not creators.
NOT_CREATORS = {
    "home",
    "posts",
    "user",
    "login",
    "signup",
    "search",
    "messages",
    "settings",
    "notifications",
    "create",
    "collection",
    "explore",
}
VANITY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(Exception):
    """The config file is missing or invalid; nothing can run."""


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


def default_config_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "patronstash" / "config.toml"


def default_data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "patronstash"


@dataclass(frozen=True)
class Backfill:
    mode: str  # "all", "none" or "since"
    since: date | None = None

    @classmethod
    def parse(cls, value) -> Backfill:
        if isinstance(value, date):
            return cls("since", value)
        if value in ("all", "none"):
            return cls(value)
        if isinstance(value, str):
            try:
                return cls("since", date.fromisoformat(value))
            except ValueError:
                pass
        raise ValueError(
            f'invalid backfill {value!r}: use "all", "none" or a date like "2024-01-01"'
        )

    def __str__(self) -> str:
        return self.since.isoformat() if self.mode == "since" else self.mode


@dataclass(frozen=True)
class Creator:
    name: str  # vanity name
    backfill: Backfill

    @property
    def url(self) -> str:
        return f"https://www.patreon.com/{self.name}"


@dataclass(frozen=True)
class Login:
    method: str  # "cookies_file" or "cookies_from_browser"
    cookies_file: Path | None = None
    browser: str | None = None
    profile: str | None = None

    def gallery_dl_cookies(self) -> str | list[str]:
        """The value for gallery-dl's `cookies` option."""
        if self.method == "cookies_file":
            return str(self.cookies_file)
        return [self.browser, self.profile] if self.profile else [self.browser]

    def describe(self) -> str:
        if self.method == "cookies_file":
            return f"cookies_file ({self.cookies_file})"
        profile = f", profile {self.profile}" if self.profile else ""
        return f"cookies_from_browser ({self.browser}{profile})"


@dataclass
class Config:
    path: Path
    download_dir: Path
    data_dir: Path
    login: Login | None
    notify_url: str | None
    notify_summary: bool
    creators: list[Creator]
    missing_backfill: list[str] = field(default_factory=list)
    passthrough: dict = field(default_factory=dict)
    reserved_keys: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_creator_name(value: str) -> str:
    """Turn a vanity name or a creator URL into the bare vanity name."""
    value = value.strip()
    if "patreon.com" in value:
        if "://" not in value:
            value = "https://" + value
        parts = urlsplit(value)
        host = parts.hostname or ""
        if host not in ("patreon.com", "www.patreon.com"):
            raise ValueError(f"not a Patreon URL: {value!r}")
        segments = [s for s in parts.path.split("/") if s]
        if segments and segments[0] in ("c", "cw"):
            segments = segments[1:]
        if not segments or segments[0].lower() in NOT_CREATORS:
            raise ValueError(f"not a creator page URL: {value!r}")
        value = segments[0]
    elif "/" in value or ":" in value:
        raise ValueError(f"not a Patreon URL: {value!r}")
    if not VANITY_RE.match(value):
        raise ValueError(f"invalid creator name: {value!r}")
    return value


def _path(value, key: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' must be a path (text)")
    return Path(value).expanduser()


def _optional_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"'{key}' must be text")
    return value or None


def _parse_login(data: dict, warnings: list[str]) -> Login | None:
    cookies_file = data.get("cookies_file")
    browser = _optional_str(data, "cookies_from_browser")
    profile = _optional_str(data, "browser_profile")

    if browser is not None:
        browser = browser.lower()
        if browser not in BROWSERS:
            raise ConfigError(
                f"cookies_from_browser: unsupported browser '{browser}' "
                f"(supported: {', '.join(BROWSERS)})"
            )

    if cookies_file is not None:
        if browser is not None:
            warnings.append(
                "both cookies_file and cookies_from_browser are set; using cookies_file"
            )
        return Login("cookies_file", cookies_file=_path(cookies_file, "cookies_file"))
    if browser is not None:
        return Login("cookies_from_browser", browser=browser, profile=profile)
    return None


def _strip_reserved(table: dict, prefix: str, found: list[str]) -> dict:
    """Copy a passthrough table without reserved keys, recording any removed."""
    result = {}
    for key, value in table.items():
        if key in RESERVED_KEYS:
            found.append(f"{prefix}.{key}")
        elif isinstance(value, dict):
            result[key] = _strip_reserved(value, f"{prefix}.{key}", found)
        else:
            result[key] = value
    return result


def _parse_creators(entries, warnings: list[str]):
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ConfigError("creators must be written as [[creator]] blocks")

    creators: list[Creator] = []
    missing: list[str] = []
    seen: set[str] = set()
    for number, entry in enumerate(entries, 1):
        raw_name = entry.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ConfigError(f"creator #{number} has no 'name'")
        try:
            name = normalize_creator_name(raw_name)
        except ValueError as exc:
            raise ConfigError(f"creator #{number}: {exc}") from None

        for key in entry.keys() - CREATOR_KEYS:
            warnings.append(f"creator '{name}': unknown key '{key}' ignored")

        if name.lower() in seen:
            warnings.append(f"creator '{name}' is listed twice; using the first entry")
            continue
        seen.add(name.lower())

        if "backfill" not in entry:
            missing.append(name)
            continue
        try:
            backfill = Backfill.parse(entry["backfill"])
        except ValueError as exc:
            raise ConfigError(f"creator '{name}': {exc}") from None
        creators.append(Creator(name, backfill))
    return creators, missing


def load_config(path: Path) -> Config:
    path = Path(path)
    try:
        with path.open("rb") as fp:
            data = tomllib.load(fp)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None

    warnings: list[str] = []
    for key in sorted(data.keys() - TOP_LEVEL_KEYS):
        warnings.append(f"unknown setting '{key}' ignored")

    if "download_dir" not in data:
        raise ConfigError("'download_dir' is not set; choose where downloads go")
    download_dir = _path(data["download_dir"], "download_dir")
    data_dir = (
        _path(data["data_dir"], "data_dir")
        if "data_dir" in data
        else default_data_dir()
    )

    login = _parse_login(data, warnings)

    notify_summary = data.get("notify_summary", False)
    if not isinstance(notify_summary, bool):
        raise ConfigError("'notify_summary' must be true or false")

    creators, missing = _parse_creators(data.get("creator", []), warnings)

    passthrough = data.get("gallery-dl", {})
    if not isinstance(passthrough, dict):
        raise ConfigError("[gallery-dl] must be a table")
    reserved: list[str] = []
    passthrough = _strip_reserved(passthrough, "gallery-dl", reserved)
    for key in reserved:
        warnings.append(f"{key} is reserved by PatronStash and was ignored")

    return Config(
        path=path,
        download_dir=download_dir,
        data_dir=data_dir,
        login=login,
        notify_url=_optional_str(data, "notify_url"),
        notify_summary=notify_summary,
        creators=creators,
        missing_backfill=missing,
        passthrough=passthrough,
        reserved_keys=reserved,
        warnings=warnings,
    )


def write_template(path: Path) -> None:
    """Create a new config file from the commented template."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    template = resources.files("patronstash").joinpath("config_template.toml")
    with path.open("x", encoding="utf-8") as fp:
        fp.write(template.read_text(encoding="utf-8"))
