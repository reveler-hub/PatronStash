import pytest

from patronstash.lock import AlreadyRunning, run_lock


def test_lock_blocks_second_run(tmp_path):
    path = tmp_path / "patronstash.lock"
    with run_lock(path):
        with pytest.raises(AlreadyRunning):
            with run_lock(path):
                pass


def test_lock_is_released_after_run(tmp_path):
    path = tmp_path / "patronstash.lock"
    with run_lock(path):
        pass
    with run_lock(path):
        pass


def test_lock_is_released_on_error(tmp_path):
    path = tmp_path / "patronstash.lock"
    with pytest.raises(RuntimeError):
        with run_lock(path):
            raise RuntimeError("boom")
    with run_lock(path):
        pass


def test_lock_records_pid(tmp_path):
    import os

    path = tmp_path / "sub" / "patronstash.lock"
    with run_lock(path):
        assert path.read_text().strip() == str(os.getpid())
