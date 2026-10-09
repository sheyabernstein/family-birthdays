import datetime as dt
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from accounts.models import Account

# How stale last_seen_at has to be before a request bothers updating it -
# a coarse "are they actually using this" signal, not real-time presence.
LAST_SEEN_THROTTLE = dt.timedelta(minutes=1)


class TrackLastSeenMiddleware:
    """Updates request.user.last_seen_at, throttled to once per LAST_SEEN_THROTTLE.

    Uses Account.objects.filter(pk=...).update(...) rather than
    request.user.save() - Account is reversion.register()-decorated, and
    a plain save() here would get swept into reversion.middleware.
    RevisionMiddleware's per-request Revision recording on every single
    page view, the same way django.contrib.auth.update_last_login avoids
    a full save() for the same field-only-update reason.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.user.is_authenticated:
            now = timezone.now()
            if request.user.last_seen_at is None or now - request.user.last_seen_at > LAST_SEEN_THROTTLE:
                Account.objects.filter(pk=request.user.pk).update(last_seen_at=now)
        return self.get_response(request)
