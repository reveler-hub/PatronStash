import pytest

from patronstash.cli import main


def test_no_command_shows_help_and_downloads_nothing(capsys, monkeypatch):
    import patronstash.runner

    monkeypatch.setattr(patronstash.runner, "run", lambda *a, **k: pytest.fail("ran"))
    assert main([]) == 0
    assert "usage: patronstash" in capsys.readouterr().out


def test_first_run_creates_template(tmp_path, capsys):
    assert main(["run"]) == 2
    path = tmp_path / "xdg-config" / "patronstash" / "config.toml"
    assert path.exists()
    assert "Created a new config file" in capsys.readouterr().err


def test_check_creates_template_too(tmp_path):
    assert main(["check"]) == 2
    assert (tmp_path / "xdg-config" / "patronstash" / "config.toml").exists()


def test_explicit_missing_config_is_an_error(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.toml"), "status"]) == 2
    assert "not found" in capsys.readouterr().err
    assert not (tmp_path / "nope.toml").exists()


def test_global_options_after_subcommand(tmp_path, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        f'download_dir = "{tmp_path}"\n[[creator]]\nname = "a"\nbackfill = "all"\n'
    )
    assert main(["status", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "a" in out and "not started" in out


def test_invalid_config_exit_code(tmp_path, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text("nonsense = = 1")
    assert main(["--config", str(cfg), "run"]) == 2
    assert "TOML" in capsys.readouterr().err
