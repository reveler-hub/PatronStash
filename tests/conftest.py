import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Tests never touch the real config or data folders (the defaults
    follow XDG_CONFIG_HOME and XDG_DATA_HOME)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


@pytest.fixture(autouse=True)
def no_github(monkeypatch):
    """Tests never contact GitHub; the update check sees it as unreachable."""

    def unreachable():
        raise OSError("network access is disabled in tests")

    monkeypatch.setattr("patronstash.updates.fetch_tags", unreachable)
