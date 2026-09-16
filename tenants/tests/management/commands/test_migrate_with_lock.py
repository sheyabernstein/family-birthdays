import socket

import pytest
from django.core.management import CommandError, call_command

from tenants.management.commands import migrate_with_lock
from tenants.management.commands.migrate_with_lock import LOCK_KEY, Command


def test_lock_id_is_hostname_prefixed_with_an_8_char_uuid_suffix(monkeypatch):
    seen_lock_id = {}

    def _fake_migrate(*args, **kwargs):
        seen_lock_id["value"] = migrate_with_lock._redis_client.get(LOCK_KEY).decode()

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
    assert migrate_with_lock._redis_client.get(LOCK_KEY) is None


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

    assert migrate_with_lock._redis_client.get(LOCK_KEY) is None


def test_raises_command_error_when_the_lock_cannot_be_acquired_in_time(monkeypatch):
    # Someone else already holds the lock, and the wait timeout is
    # patched to a value the very first (failed) attempt already
    # exceeds - no need to actually wait for the real 600-second default.
    migrate_with_lock._redis_client.set(LOCK_KEY, "other-lock-id", ex=300)
    monkeypatch.setattr("tenants.management.commands.migrate_with_lock.LOCK_WAIT_TIMEOUT", -1)

    with pytest.raises(CommandError, match="Migration lock unavailable"):
        call_command("migrate_with_lock")


def test_waits_for_the_lock_to_be_released_then_acquires_it(monkeypatch):
    # Held by "another pod" when the command starts - time.sleep is
    # patched to release it (simulating that other pod finishing) rather
    # than actually sleeping, so the retry loop's next set(nx=True) call
    # succeeds immediately instead of after a real wait.
    migrate_with_lock._redis_client.set(LOCK_KEY, "other-lock-id", ex=300)

    def _fake_sleep(_seconds):
        migrate_with_lock._redis_client.delete(LOCK_KEY)

    monkeypatch.setattr("tenants.management.commands.migrate_with_lock.time.sleep", _fake_sleep)
    monkeypatch.setattr("tenants.management.commands.migrate_with_lock.call_command", lambda *a, **k: None)

    call_command("migrate_with_lock")

    assert migrate_with_lock._redis_client.get(LOCK_KEY) is None


def test_release_lock_swallows_a_redis_error_instead_of_raising(monkeypatch):
    migrate_with_lock._redis_client.set(LOCK_KEY, "some-lock-id", ex=300)

    def _raise(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(migrate_with_lock._redis_client, "pipeline", _raise)

    command = Command()
    command._lock_id = "some-lock-id"
    command._release_lock()


def test_release_lock_does_not_delete_a_lock_acquired_by_someone_else():
    # Simulates a lock that expired mid-migration and was re-acquired by a
    # different pod before this process's own (stale) release ran - the
    # compare-and-delete unlock script must leave the new holder's lock
    # alone instead of deleting it out from under them.
    migrate_with_lock._redis_client.set(LOCK_KEY, "the-new-holders-id", ex=300)

    command = Command()
    command._lock_id = "a-stale-id-from-an-earlier-acquisition"
    command._release_lock()

    assert migrate_with_lock._redis_client.get(LOCK_KEY) == b"the-new-holders-id"
