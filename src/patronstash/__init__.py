"""PatronStash: archive content from the Patreon creators you support."""

from importlib.metadata import PackageNotFoundError, version

# The version lives in pyproject.toml only; bump it there for each release.
try:
    __version__ = version("patronstash")
except PackageNotFoundError:  # a source checkout that was never installed
    __version__ = "unknown"
