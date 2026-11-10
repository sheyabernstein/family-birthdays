from pathlib import Path

import pytest
from celery.signals import heartbeat_sent, worker_ready, worker_shutdown
from freezegun import freeze_time

import config.celery  # noqa: F401 - what connects the heartbeat_sent receiver in production
from config import health


@pytest.fixture
def health_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(health, "HEALTH_DIR", tmp_path)
    return tmp_path


def _names(directory: Path) -> set[str]:
    return {entry.name for entry in directory.iterdir()}


@freeze_time("2026-11-10 12:00:00")
def test_touch_writes_the_current_epoch(health_dir):
    health.touch(health.WORKER_ALIVE)

    assert (health_dir / health.WORKER_ALIVE).read_text().strip() == "1794312000"


def test_touch_leaves_no_temp_file_behind(health_dir):
    health.touch(health.WORKER_ALIVE)
    health.touch(health.WORKER_ALIVE)

    assert _names(health_dir) == {health.WORKER_ALIVE}


def test_touch_swallows_a_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HEALTH_DIR", tmp_path / "does-not-exist")

    health.touch(health.WORKER_ALIVE)


@pytest.mark.parametrize(
    ["signal", "expected_files"],
    [
        [heartbeat_sent, {health.WORKER_ALIVE}],
        [worker_ready, {health.WORKER_ALIVE, health.WORKER_READY}],
    ],
    ids=[
        "a heartbeat touches only the alive file",
        "worker ready also creates the ready file",
    ],
)
def test_signal_writes_its_health_files(health_dir, signal, expected_files):
    signal.send(sender=None)

    assert _names(health_dir) == expected_files


def test_worker_shutdown_removes_every_health_file(health_dir):
    health.touch(health.WORKER_ALIVE)
    health.touch(health.WORKER_READY)

    worker_shutdown.send(sender=None)

    assert _names(health_dir) == set()


def test_worker_shutdown_swallows_a_removal_failure(health_dir, monkeypatch):
    def deny(self, missing_ok=False):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", deny)

    # Called directly - signal.send() would log and swallow the error itself.
    health._on_worker_shutdown()


def test_heartbeat_receiver_is_connected_by_importing_the_celery_app():
    # Celery's Heart only binds heartbeat_sent if it already has a receiver
    # when the consumer builds it - connecting later silently never fires.
    assert heartbeat_sent.receivers
