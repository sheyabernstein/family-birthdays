"""prometheus_client multiprocess-mode setup, shared by gunicorn and Celery.

Both servers are prefork: gunicorn's own workers and Celery's own worker
pool each fork children that need their own per-pid metric `.db` files,
aggregated across processes by prometheus_client's `multiprocess` module.
Ported from a FastAPI service (prom-gateway) that already uses this exact
pattern for the same reason.
"""

import atexit
import os
from pathlib import Path

from config.helpers import get_env_bool

_PURGE_ENV = "PROMETHEUS_MULTIPROC_WIPE"

_initialized = False


def _purge_stale_db_files(directory: Path) -> int:
    """Remove all `*.db` files in `directory`. Returns count removed."""
    count = 0
    for path in directory.glob("*.db"):
        try:
            path.unlink()
            count += 1
        except OSError:
            # Best-effort: if another worker raced us we don't care.
            pass
    return count


def init_multiprocess_dir() -> str:
    """Ensure `PROMETHEUS_MULTIPROC_DIR` is set and writable.

    Does not purge old *.db files by default (the container's /tmp is
    already fresh on every restart). Set PROMETHEUS_MULTIPROC_WIPE=1 to
    purge on init (for local dev, where /tmp can persist across restarts
    of a long-running dev container).

    Idempotent across calls/imports.
    """
    global _initialized
    directory = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "/tmp/prom_multiproc")
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = directory

    if _initialized:
        return directory

    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)

    if get_env_bool(_PURGE_ENV, False):
        _purge_stale_db_files(path)

    _initialized = True
    return directory


def register_atexit_mark_dead() -> None:
    """Registers an atexit hook that marks the current pid dead.

    `multiprocess.mark_process_dead` archives the per-pid `*.db` files into
    the process-wide aggregate so subsequent scrapes still see the values,
    then removes the per-pid live files. Without this, dead-worker files
    linger until the next container restart's directory purge. Called once
    per child process, post-fork - see config/gunicorn_conf.py's
    `post_fork` hook and config/celery.py's `worker_process_init` receiver.
    """
    # Import lazily so that init_multiprocess_dir() runs first.
    from prometheus_client import multiprocess

    pid = os.getpid()

    def _mark_dead() -> None:
        try:
            multiprocess.mark_process_dead(pid)
        except Exception:  # atexit must never raise
            pass

    atexit.register(_mark_dead)
