"""A lock file so that two runs never overlap."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(Exception):
    """Another PatronStash run holds the lock."""


@contextmanager
def run_lock(path: Path):
    """Hold an exclusive lock on `path` for the duration of the block.

    The lock is an flock(), so the kernel drops it if the process dies;
    a stale lock file left behind by a crash never blocks later runs.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fp:
        try:
            fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning(str(path)) from None
        try:
            fp.seek(0)
            fp.truncate()
            fp.write(f"{os.getpid()}\n")
            fp.flush()
            yield
        finally:
            fcntl.flock(fp, fcntl.LOCK_UN)
