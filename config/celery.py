import os

from celery import Celery
from celery.signals import setup_logging, worker_process_init
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("family_birthdays")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Wires task_prerun/task_postrun/task_failure/task_retry to Prometheus task
# metrics + a Sentry capture fallback - importing for its side effect of
# connecting the receivers, same pattern as _use_structlog below.
import config.observability.celery_signals  # noqa: E402,F401


@worker_process_init.connect
def _init_observability_per_worker(**_kwargs) -> None:
    """Re-initializes OTel + Prometheus multiprocess state post-fork.

    Celery's prefork pool forks worker child processes after this module
    is imported in the parent - a BatchSpanProcessor's background export
    thread and prometheus_client's per-pid .db files both need to be set
    up in the child, not inherited from the parent across fork(). See
    config/observability/tracing.py's own docstring for the full reasoning.
    """
    from config.observability.metrics import set_build_info
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
