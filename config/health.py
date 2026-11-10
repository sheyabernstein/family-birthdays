"""Heartbeat files the worker's probes read instead of running Celery.

`celery inspect ping` starts a fresh Django/Celery interpreter per probe.
The worker instead writes a timestamp to a tiny file, and
docker/entrypoints/worker_healthcheck.sh only compares it to the clock.

The writer runs on the worker's main thread, so a wedged event loop stops
the file going fresh. So does a broker outage, since Celery's reconnect
backoff sleeps on that same thread - which is why the max age is long enough
to ride out a brief Redis blip.
"""

import os
import time
from pathlib import Path

from celery.signals import heartbeat_sent, worker_ready, worker_shutdown

from config.logging_config import logger

WORKER_ALIVE = "worker-alive"
WORKER_READY = "worker-ready"

HEALTH_DIR = Path(os.getenv("WORKER_HEALTH_DIR", "/tmp"))

# The same number is the default in docker/entrypoints/worker_healthcheck.sh.
HEARTBEAT_MAX_AGE_SECONDS = 600


def touch(name: str) -> None:
    """Records the current epoch in a health file, atomically.

    Writes a temp file and renames it so a probe never reads a half-written
    value. A failure is logged and swallowed - a health file must never be
    the thing that crashes the worker.

    Args:
        name: One of the file-name constants above, relative to HEALTH_DIR.
    """
    target = HEALTH_DIR / name
    temp = HEALTH_DIR / f".{name}.tmp"
    try:
        temp.write_text(f"{int(time.time())}\n")
        os.replace(temp, target)
    except OSError as exc:
        logger.warning("could not write health file", path=str(target), exc_info=exc)


@heartbeat_sent.connect
def _on_heartbeat_sent(**_kwargs) -> None:
    touch(WORKER_ALIVE)


@worker_ready.connect
def _on_worker_ready(**_kwargs) -> None:
    touch(WORKER_READY)
    touch(WORKER_ALIVE)


@worker_shutdown.connect
def _on_worker_shutdown(**_kwargs) -> None:
    for name in (WORKER_ALIVE, WORKER_READY):
        (HEALTH_DIR / name).unlink(missing_ok=True)
