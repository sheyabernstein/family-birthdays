import datetime as dt

import pytest
import reversion
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.utils import timezone

from accounts.middleware import LAST_SEEN_THROTTLE, TrackLastSeenMiddleware
from accounts.models import Account

pytestmark = pytest.mark.django_db


def test_sets_last_seen_at_on_first_request(rf):
    account = Account.objects.create_user(email="first-time@example.com")
    request = rf.get("/")
    request.user = account
    middleware = TrackLastSeenMiddleware(lambda r: HttpResponse())

    middleware(request)

    account.refresh_from_db()
    assert account.last_seen_at is not None


def test_does_not_update_within_the_throttle_window(rf):
    account = Account.objects.create_user(email="recent@example.com")
    recent = timezone.now() - (LAST_SEEN_THROTTLE / 2)
    Account.objects.filter(pk=account.pk).update(last_seen_at=recent)
    account.refresh_from_db()
    request = rf.get("/")
    request.user = account
    middleware = TrackLastSeenMiddleware(lambda r: HttpResponse())

    middleware(request)

    account.refresh_from_db()
    assert account.last_seen_at == recent


def test_updates_once_the_throttle_window_has_passed(rf):
    account = Account.objects.create_user(email="stale@example.com")
    stale = timezone.now() - LAST_SEEN_THROTTLE - dt.timedelta(seconds=1)
    Account.objects.filter(pk=account.pk).update(last_seen_at=stale)
    account.refresh_from_db()
    request = rf.get("/")
    request.user = account
    middleware = TrackLastSeenMiddleware(lambda r: HttpResponse())

    middleware(request)

    account.refresh_from_db()
    assert account.last_seen_at > stale


def test_does_not_touch_an_anonymous_request(rf):
    request = rf.get("/")
    request.user = AnonymousUser()
    middleware = TrackLastSeenMiddleware(lambda r: HttpResponse())

    # Should not raise - AnonymousUser has no last_seen_at/pk to update against.
    middleware(request)


def test_does_not_create_a_reversion_version(rf):
    """Regression guard for the whole reason this uses .update() instead
    of .save() - see TrackLastSeenMiddleware's own docstring."""
    account = Account.objects.create_user(email="versioned@example.com")
    request = rf.get("/")
    request.user = account
    middleware = TrackLastSeenMiddleware(lambda r: HttpResponse())

    with reversion.create_revision():
        middleware(request)

    assert reversion.models.Version.objects.get_for_object(account).count() == 0
