"""Fixtures shared across more than one app's tests. Anything used by
only one test module lives in that module; anything shared within a
single app's tests lives in that app's tests/conftest.py - see AGENTS.md's
"Test layout" section."""

import fakeredis
import pytest

from accounts import magic_links
from accounts.models import Account
from notifications import sms
from tenants.management.commands import migrate_with_lock
from tenants.models import Family, FamilyMembership


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """Every test gets its own in-process fake Redis, so the suite never
    needs a real Redis server - see accounts/magic_links.py,
    tenants/management/commands/migrate_with_lock.py, and
    notifications/sms.py, the modules that talk to Redis directly
    (separate fake instances - nothing relies on them sharing keyspace)."""
    monkeypatch.setattr(magic_links, "_redis_client", fakeredis.FakeStrictRedis())
    monkeypatch.setattr(migrate_with_lock, "_redis_client", fakeredis.FakeStrictRedis())
    monkeypatch.setattr(sms, "_redis_client", fakeredis.FakeStrictRedis())


@pytest.fixture
def family():
    return Family.objects.create(name="Test Family")


@pytest.fixture
def two_families():
    family_a = Family.objects.create(name="Rokach Family")
    family_b = Family.objects.create(name="Bernstein Family")
    account_a = Account.objects.create_user(email="a@example.com")
    account_b = Account.objects.create_user(email="b@example.com")
    FamilyMembership.objects.create(account=account_a, family=family_a, role=FamilyMembership.Role.OWNER)
    FamilyMembership.objects.create(account=account_b, family=family_b, role=FamilyMembership.Role.OWNER)
    return family_a, family_b, account_a, account_b
