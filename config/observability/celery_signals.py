"""Wires Celery's own task signals to Prometheus task metrics + Sentry.

Imported (once) from config/celery.py, at module import time - connecting
these receivers is a side effect of importing this module, mirroring how
config/logging_config.py's own module-level `dictConfig()` call already
works in this codebase.
"""

import time

import sentry_sdk
from celery import Task
from celery.signals import task_failure, task_postrun, task_prerun, task_retry

from config.exceptions import FamilyBirthdaysError
from config.observability import metrics

_start_times: dict[str, float] = {}


@task_prerun.connect
def _on_task_prerun(task_id: str, task: Task, **_kwargs) -> None:
    metrics.celery_tasks_in_progress.labels(task_name=task.name).inc()
    _start_times[task_id] = time.perf_counter()


@task_postrun.connect
def _on_task_postrun(task_id: str, task: Task, state: str, **_kwargs) -> None:
    metrics.celery_tasks_in_progress.labels(task_name=task.name).dec()

    start = _start_times.pop(task_id, None)
    # SUCCESS/FAILURE/RETRY are Celery's own result states - task_failure
    # (below) is what actually increments celery_tasks_total for a failure,
    # since task_postrun's own "state" can lag behind a retry decision;
    # this receiver only records duration and the success count.
    status = "success" if state == "SUCCESS" else "other"
    if start is not None:
        metrics.celery_task_duration_seconds.labels(task_name=task.name, status=status).observe(
            amount=time.perf_counter() - start
        )
    if state == "SUCCESS":
        metrics.celery_tasks_total.labels(task_name=task.name, status="success").inc()


@task_failure.connect
def _on_task_failure(sender: Task, task_id: str, exception: BaseException, **_kwargs) -> None:
    metrics.celery_tasks_total.labels(task_name=sender.name, status="failure").inc()
    # Celery task failures never flow through a WSGI request/response cycle
    # (no config.observability.django_middleware, no
    # process_exception hook) - this is the guaranteed fallback that gets
    # a failed task's exception into Sentry regardless of whether OTel's
    # Celery instrumentation also records it onto its own task span (which
    # config.observability.sentry's patched Span.record_exception would
    # otherwise catch automatically). Same FamilyBirthdaysError exclusion
    # as that patched method - a task re-raising its own already-handled
    # domain error (e.g. SmsUnrecoverableError) shouldn't page anyone
    # twice, once from each of these two independent capture paths.
    if not isinstance(exception, FamilyBirthdaysError):
        sentry_sdk.capture_exception(exception)


@task_retry.connect
def _on_task_retry(sender: Task, **_kwargs) -> None:
    metrics.celery_tasks_total.labels(task_name=sender.name, status="retry").inc()
