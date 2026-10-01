import os
import signal
import time

from celery import Celery
from celery.signals import setup_logging, worker_process_init
from django.conf import settings
from redbeat.schedulers import RedBeatScheduler
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from config.logging_config import logger

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("family_birthdays")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Wires task_prerun/task_postrun/task_failure/task_retry to Prometheus task
# metrics + a Sentry capture fallback - importing for its side effect of
# connecting the receivers, same pattern as _use_structlog below.
import config.observability.celery_signals  # noqa: E402,F401


class ResilientRedBeatScheduler(RedBeatScheduler):
    """RedBeatScheduler, but a transient Redis timeout doesn't kill Beat forever.

    tick() extends its distributed lock with no exception handling around
    it at all, and `celery worker --beat` runs Beat as a real forked
    child process, not a thread - an uncaught connection timeout there
    kills that child with nothing left to ever restart it, while the
    worker it forked from keeps running none the wiser. This retries a
    few times, then kills the parent process too (same fork relationship,
    via os.getppid()) before re-raising, so an unrecoverable failure is
    an ordinary container crash Kubernetes already knows how to restart,
    not a silently dead scheduler.
    """

    TICK_MAX_ATTEMPTS = 3
    TICK_RETRY_DELAY_SECONDS = 2

    def tick(self, *args, **kwargs) -> float:
        for attempt in range(1, self.TICK_MAX_ATTEMPTS + 1):
            try:
                return super().tick(*args, **kwargs)
            except (RedisConnectionError, RedisTimeoutError) as exc:
                if attempt < self.TICK_MAX_ATTEMPTS:
                    logger.warning(
                        "beat tick failed reaching Redis, retrying",
                        attempt=attempt,
                        max_attempts=self.TICK_MAX_ATTEMPTS,
                        exc_info=exc,
                    )
                    time.sleep(self.TICK_RETRY_DELAY_SECONDS)
                    continue
                logger.error(
                    "beat tick exhausted retries reaching Redis - killing the worker process",
                    attempt=attempt,
                    exc_info=exc,
                )
                os.kill(os.getppid(), signal.SIGTERM)
                raise


@worker_process_init.connect
def _init_observability_per_worker(**_kwargs) -> None:
    """Re-initializes OTel + Prometheus multiprocess state post-fork.

    Celery's prefork pool forks worker child processes after this module
    is imported in the parent - a BatchSpanProcessor's background export
    thread and prometheus_client's per-pid .db files both need to be set
    up in the child, not inherited from the parent across fork(). See
    config/observability/tracing.py's own docstring for the full reasoning.
    """
    from config.observability.metrics import preregister_notification_counters, set_build_info
    from config.observability.multiproc import init_multiprocess_dir, register_atexit_mark_dead
    from config.observability.tracing import init_tracing

    # init_multiprocess_dir() first, always - prometheus_client's
    # multiprocess mode needs this directory to actually exist by the time
    # a metric is first written, not just PROMETHEUS_MULTIPROC_DIR set in
    # the environment. The Dockerfile happens to pre-create it, which is
    # why this worked without this call - but nothing about running the
    # Celery worker itself guaranteed that, unlike gunicorn's own
    # post_fork hook (config/gunicorn_conf.py), which always calls this
    # first for exactly this reason.
    init_multiprocess_dir()
    init_tracing()
    register_atexit_mark_dead()
    set_build_info(settings.BUILD_VERSION)
    # Only a worker task ever sends an email/SMS, never a web request -
    # so this is the only place these need pre-registering.
    preregister_notification_counters()


@setup_logging.connect
def _use_structlog(**_kwargs) -> None:
    """Route every Celery log line through the structlog JSON pipeline.

    Celery's worker boot otherwise runs its own logging setup - hijacking the
    root logger and wrapping records in its text TaskFormatter, which is why
    worker/beat startup lines came out as plain text and our JSON logs got a
    `[timestamp: LEVEL/Process]` prefix stuck on the front. Connecting any
    receiver to this signal makes Celery skip that setup entirely. Importing
    Django settings above already imported config/logging_config.py (whose
    module-level code applied it), so by the time this fires the one shared
    JSON pipeline is the only logging config in play and everything -
    startup included - flows through it.
    """
