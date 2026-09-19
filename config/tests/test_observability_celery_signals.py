from unittest.mock import MagicMock, patch

from config.exceptions import FamilyBirthdaysError
from config.observability import metrics
from config.observability.celery_signals import (
    _on_task_failure,
    _on_task_postrun,
    _on_task_prerun,
    _on_task_retry,
    _start_times,
)
from config.tests.conftest import sample_value


class _DomainError(FamilyBirthdaysError):
    pass


def test_prerun_increments_in_progress_and_records_a_start_time():
    task = MagicMock(name="task", spec=["name"])
    task.name = "myapp.tasks.do_thing"
    before = sample_value(metrics.celery_tasks_in_progress, task_name=task.name)

    _on_task_prerun(task_id="abc123", task=task)

    assert sample_value(metrics.celery_tasks_in_progress, task_name=task.name) == before + 1
    assert "abc123" in _start_times
    _start_times.pop("abc123", None)


def test_postrun_decrements_in_progress_and_records_success():
    task = MagicMock(name="task", spec=["name"])
    task.name = "myapp.tasks.do_thing_success"
    _on_task_prerun(task_id="task-1", task=task)
    before_total = sample_value(metrics.celery_tasks_total, task_name=task.name, status="success")

    _on_task_postrun(task_id="task-1", task=task, state="SUCCESS")

    assert sample_value(metrics.celery_tasks_in_progress, task_name=task.name) == 0
    assert sample_value(metrics.celery_tasks_total, task_name=task.name, status="success") == before_total + 1
    assert "task-1" not in _start_times


def test_postrun_does_not_count_a_non_success_state_as_a_completion():
    """task_failure (below), not task_postrun, is what increments
    celery_tasks_total for a failure - postrun's own "state" can lag
    behind a retry decision."""
    task = MagicMock(name="task", spec=["name"])
    task.name = "myapp.tasks.do_thing_failure"
    _on_task_prerun(task_id="task-2", task=task)
    before_total = sample_value(metrics.celery_tasks_total, task_name=task.name, status="success")

    _on_task_postrun(task_id="task-2", task=task, state="FAILURE")

    assert sample_value(metrics.celery_tasks_total, task_name=task.name, status="success") == before_total


@patch("config.observability.celery_signals.sentry_sdk.capture_exception")
def test_task_failure_reports_a_genuine_bug_to_sentry(mock_capture):
    sender = MagicMock(name="sender", spec=["name"])
    sender.name = "myapp.tasks.flaky"
    before = sample_value(metrics.celery_tasks_total, task_name=sender.name, status="failure")

    _on_task_failure(sender=sender, task_id="task-3", exception=ValueError("boom"))

    assert sample_value(metrics.celery_tasks_total, task_name=sender.name, status="failure") == before + 1
    mock_capture.assert_called_once()


@patch("config.observability.celery_signals.sentry_sdk.capture_exception")
def test_task_failure_skips_sentry_for_a_familybirthdayserror(mock_capture):
    sender = MagicMock(name="sender", spec=["name"])
    sender.name = "myapp.tasks.deliberate_failure"

    _on_task_failure(sender=sender, task_id="task-4", exception=_DomainError("already handled"))

    mock_capture.assert_not_called()


def test_task_retry_increments_the_retry_counter():
    sender = MagicMock(name="sender", spec=["name"])
    sender.name = "myapp.tasks.retrying"
    before = sample_value(metrics.celery_tasks_total, task_name=sender.name, status="retry")

    _on_task_retry(sender=sender)

    assert sample_value(metrics.celery_tasks_total, task_name=sender.name, status="retry") == before + 1
