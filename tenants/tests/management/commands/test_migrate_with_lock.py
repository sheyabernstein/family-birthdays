import socket
import threading
import time

import pytest
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django_redis import get_redis_connection

from tenants.management.commands.migrate_with_lock import LOCK_KEY, Command

_redis = get_redis_connection("default")


def _lock_value() -> str | None:
    # cache.lock()'s token is a raw SET, not run through django-redis's own
    # pickle serializer - cache.get(LOCK_KEY) would throw UnpicklingError on
    # an actively-held lock, so tests read it back the same way
    # migrate_with_lock.py's own owner-logging peek does.
    raw = _redis.get(cache.make_key(LOCK_KEY))
    return raw.decode() if raw is not None else None


def test_lock_id_is_hostname_prefixed_with_an_8_char_uuid_suffix(monkeypatch):
    seen_lock_id = {}

    def _fake_migrate(*args, **kwargs):
        seen_lock_id["value"] = _lock_value()

    monkeypatch.setattr("tenants.management.commands.migrate_with_lock.call_command", _fake_migrate)

    call_command("migrate_with_lock")

    hostname, _, uid = seen_lock_id["value"].partition(":")
    assert hostname == socket.gethostname()
    assert len(uid) == 8


def test_acquires_the_lock_runs_migrate_and_releases_the_lock(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tenants.management.commands.migrate_with_lock.call_command",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    call_command("migrate_with_lock")

    assert calls == [(("migrate",), {"interactive": False, "verbosity": 1})]
    assert _lock_value() is None


def test_passes_app_label_and_migration_name_through_to_migrate(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tenants.management.commands.migrate_with_lock.call_command",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    call_command("migrate_with_lock", "tenants", "0001_initial")

    assert calls == [(("migrate", "tenants", "0001_initial"), {"interactive": False, "verbosity": 1})]


def test_releases_the_lock_even_when_migrate_raises(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("tenants.management.commands.migrate_with_lock.call_command", _raise)

    with pytest.raises(RuntimeError):
        call_command("migrate_with_lock")

    assert _lock_value() is None


def test_raises_command_error_when_the_lock_cannot_be_acquired_in_time(monkeypatch):
    # A real second lock instance stands in for "another pod already holds
    # it" - the wait timeout is patched to a value the very first (failed)
    # attempt already exceeds, so there's no need to actually wait out the
    # real 600-second default.
    other_pod_lock = cache.lock(LOCK_KEY, timeout=300)
    other_pod_lock.acquire()
    monkeypatch.setattr("tenants.management.commands.migrate_with_lock.LOCK_WAIT_TIMEOUT", -1)

    with pytest.raises(CommandError, match="Migration lock unavailable"):
        call_command("migrate_with_lock")

    other_pod_lock.release()


def test_waits_for_the_lock_to_be_released_then_acquires_it(monkeypatch):
    # Held by "another pod" when the command starts; a background thread
    # releases it shortly after, so the command's own blocking acquire()
    # (redis-py's real retry/poll loop, not a hand-rolled one) succeeds
    # once it actually becomes free instead of timing out.
    monkeypatch.setattr("tenants.management.commands.migrate_with_lock.LOCK_CHECK_INTERVAL", 0.05)
    monkeypatch.setattr("tenants.management.commands.migrate_with_lock.LOCK_WAIT_TIMEOUT", 2)
    monkeypatch.setattr("tenants.management.commands.migrate_with_lock.call_command", lambda *a, **k: None)

    # thread_local=False: acquire() and release() happen on different
    # threads here (standing in for different processes/pods), and
    # redis-py's Lock otherwise stores its token in thread-local storage -
    # release() from a different thread than acquire() would silently find
    # no token there and raise inside the background thread, which the
    # main thread would never see, and just wait out the full timeout.
    other_pod_lock = cache.lock(LOCK_KEY, timeout=300, thread_local=False)
    other_pod_lock.acquire()

    def _release_soon():
        time.sleep(0.1)
        other_pod_lock.release()

    threading.Thread(target=_release_soon).start()

    call_command("migrate_with_lock")

    assert _lock_value() is None


def test_release_lock_swallows_an_error_instead_of_raising(monkeypatch):
    command = Command()
    command._lock = cache.lock(LOCK_KEY, timeout=300)
    command._lock.acquire()

    def _raise():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(command._lock, "release", _raise)

    command._release_lock()  # must not raise


def test_release_lock_does_not_delete_a_lock_acquired_by_someone_else():
    # Simulates a lock that expired mid-migration and was re-acquired by a
    # different pod before this process's own (stale) release ran - the
    # Lua compare-and-delete release script must leave the new holder's
    # lock alone instead of deleting it out from under them.
    command = Command()
    command._lock = cache.lock(LOCK_KEY, timeout=300)
    command._lock.acquire()

    _redis.set(cache.make_key(LOCK_KEY), "the-new-holders-id")

    command._release_lock()

    assert _lock_value() == "the-new-holders-id"
