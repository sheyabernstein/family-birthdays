"""Fixtures shared across more than one app's tests. Anything used by
only one test module lives in that module; anything shared within a
single app's tests lives in that app's tests/conftest.py - see AGENTS.md's
"Test layout" section."""

import pytest
from django.core.cache import cache

from accounts.models import Account
from tenants.models import Family, FamilyMembership


@pytest.fixture(autouse=True)
def _clear_cache():
    """Resets the (fakeredis-backed, in tests - see config/settings_test.py) cache before every test.

    fakeredis is one process-wide fake store for the whole suite, not a
    fresh connection per test - without this, a key like
    migrate_with_lock's fixed LOCK_KEY would leak state between tests
    that run in the same process.
    """
    cache.clear()


@pytest.fixture
def family():
    # email_sending_enabled/sms_sending_enabled default to False (a
    # site-admin-only kill switch, off until explicitly turned on - see
    # Family's own docstring) - True here so the vast majority of tests,
    # which are about notification *content*/*audience* logic rather
    # than this tenant-wide switch itself, don't all have to opt back in
    # individually. Tests that actually exercise the switch (see
    # notifications/tests/test_audience.py) build their own Family with
    # it explicitly left off instead of using this fixture.
    return Family.objects.create(name="Test Family", email_sending_enabled=True, sms_sending_enabled=True)


@pytest.fixture
def two_families():
    family_a = Family.objects.create(
        name="Rokach Family", email_sending_enabled=True, sms_sending_enabled=True
    )
    family_b = Family.objects.create(
        name="Bernstein Family", email_sending_enabled=True, sms_sending_enabled=True
    )
    account_a = Account.objects.create_user(email="a@example.com")
    account_b = Account.objects.create_user(email="b@example.com")
    FamilyMembership.objects.create(account=account_a, family=family_a, role=FamilyMembership.Role.OWNER)
    FamilyMembership.objects.create(account=account_b, family=family_b, role=FamilyMembership.Role.OWNER)
    return family_a, family_b, account_a, account_b
