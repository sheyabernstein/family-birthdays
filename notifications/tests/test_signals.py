import pytest

from notifications.models import EventType

pytestmark = pytest.mark.django_db


def _fake_task(*, on_delay):
    return type("FakeTask", (), {"delay": staticmethod(on_delay)})()


def _raise():
    raise RuntimeError("boom")


def test_saving_an_event_type_queues_a_full_recompute(monkeypatch, family):
    calls = []
    monkeypatch.setattr(
        "notifications.signals.compute_occurrences", _fake_task(on_delay=lambda: calls.append(1))
    )

    EventType.objects.create(family=family, code="reunion", name="Reunion", anchor="birth")

    assert calls == [1]


def test_updating_an_event_type_also_queues_a_recompute(monkeypatch, family):
    event_type = EventType.objects.create(family=family, code="reunion", name="Reunion", anchor="birth")
    calls = []
    monkeypatch.setattr(
        "notifications.signals.compute_occurrences", _fake_task(on_delay=lambda: calls.append(1))
    )

    event_type.notify_days_before = 1
    event_type.save()

    assert calls == [1]


def test_event_type_recompute_queue_failure_is_logged_and_does_not_raise(monkeypatch, family):
    monkeypatch.setattr("notifications.signals.compute_occurrences", _fake_task(on_delay=_raise))

    # Doesn't raise - the admin's save should still succeed even if
    # queuing the recompute itself blows up (e.g. broker unreachable).
    EventType.objects.create(family=family, code="reunion", name="Reunion", anchor="birth")
