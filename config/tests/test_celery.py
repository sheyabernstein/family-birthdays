import signal
from unittest.mock import patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from config.celery import ResilientRedBeatScheduler


def _bare_scheduler() -> ResilientRedBeatScheduler:
    """A ResilientRedBeatScheduler with __init__ skipped - it would otherwise try to reach Redis."""
    return object.__new__(ResilientRedBeatScheduler)


def test_tick_passes_through_on_success():
    scheduler = _bare_scheduler()
    with patch("redbeat.schedulers.RedBeatScheduler.tick", return_value=5.0) as mock_tick:
        assert scheduler.tick() == 5.0
    mock_tick.assert_called_once()


def test_tick_retries_and_recovers_from_a_transient_timeout(monkeypatch):
    monkeypatch.setattr("config.celery.time.sleep", lambda seconds: None)
    scheduler = _bare_scheduler()
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < ResilientRedBeatScheduler.TICK_MAX_ATTEMPTS:
            raise RedisConnectionError("boom")
        return 5.0

    with patch("redbeat.schedulers.RedBeatScheduler.tick", side_effect=flaky):
        assert scheduler.tick() == 5.0
    assert len(attempts) == ResilientRedBeatScheduler.TICK_MAX_ATTEMPTS


def test_tick_kills_the_parent_process_once_retries_are_exhausted(monkeypatch):
    monkeypatch.setattr("config.celery.time.sleep", lambda seconds: None)
    monkeypatch.setattr("config.celery.os.getppid", lambda: 4242)
    killed = {}
    monkeypatch.setattr("config.celery.os.kill", lambda pid, sig: killed.update(pid=pid, sig=sig))
    scheduler = _bare_scheduler()

    with patch("redbeat.schedulers.RedBeatScheduler.tick", side_effect=RedisConnectionError("boom")):
        with pytest.raises(RedisConnectionError):
            scheduler.tick()

    assert killed == {"pid": 4242, "sig": signal.SIGTERM}
