"""Prometheus HTTP request metrics middleware.

The WSGI/sync equivalent of prom-gateway's ASGI metrics middleware: wraps
each request to track in-flight count, latency, completion count, and
unhandled exceptions.
"""

import re
import time
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.urls import Resolver404, resolve
from opentelemetry.instrumentation.utils import suppress_instrumentation

from config.observability import metrics

# Docker's own healthcheck (config/urls.py's /readyz, hit every 10s) and,
# since WhiteNoise serves static (and, once it exists, media) assets
# in-process as ordinary Django middleware rather than a separate server,
# every CSS/JS/icon request on a page load too - none of these are real
# application traffic worth a metrics series or its own trace/span. The
# one definition of *what's* excluded - both _is_excluded below and
# config.observability.tracing's DjangoInstrumentor wiring are built from
# these same two tuples, so there's exactly one place that knows the
# actual list of paths/prefixes, even though (see EXCLUDED_URLS_PATTERN's
# own comment) the two consumers need differently-shaped regexes to
# express it correctly.
_EXACT_EXCLUDED_PATHS = ("/healthz", "/readyz")
_PREFIX_EXCLUDED_PATHS = (settings.STATIC_URL, settings.MEDIA_URL)

_IS_EXCLUDED_REGEX = re.compile(
    "|".join(
        (
            *(rf"^{re.escape(path)}$" for path in _EXACT_EXCLUDED_PATHS),
            *(rf"^{re.escape(prefix)}" for prefix in _PREFIX_EXCLUDED_PATHS),
        )
    )
)

# For config.observability.tracing's DjangoInstrumentor(excluded_urls=...)
# wiring - *not* the same pattern _is_excluded uses below, verified live
# against a running container: opentelemetry-instrumentation-django's own
# otel_middleware.py matches excluded_urls against request.build_absolute
# _uri("?") (the *full* "scheme://host/path?" URL), not request.path, so
# a `^`-anchored path-only pattern like _is_excluded's never matches
# anything there - silently leaving every "excluded" request fully traced
# instead (confirmed: an earlier version of this pattern reused _is_
# excluded's own path-anchored form here, and every static/healthz/readyz
# request still got a real, non-zero trace_id). `://[^/]*` stands in for
# the unpredictable scheme+host prefix Django always inserts before the
# path; `(?:\?|$)` after an exact path keeps it from also matching some
# other route that merely *ends* with the same segment (e.g. "/healthz"
# inside "/foo/healthz") without needing to know what the whole absolute
# URL looks like.
EXCLUDED_URLS_PATTERN = ",".join(
    (
        *(rf"://[^/]*{re.escape(path)}(?:\?|$)" for path in _EXACT_EXCLUDED_PATHS),
        *(rf"://[^/]*{re.escape(prefix)}" for prefix in _PREFIX_EXCLUDED_PATHS),
    )
)
UNMATCHED = "unmatched"


def _is_excluded(path: str) -> bool:
    return bool(_IS_EXCLUDED_REGEX.search(path))


def _resolve_route(request: HttpRequest) -> tuple[str, str]:
    """Resolves (route, tag) up front, mirroring what Django itself resolves internally during dispatch.

    Needed so the in-progress gauge reflects the real route during the
    in-flight window, not just at completion - `request.resolver_match`
    isn't populated on the request object until Django's own dispatch runs
    inside `get_response()`, which is too late for the "before" half of an
    in-progress gauge.
    """
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return UNMATCHED, ""
    return match.url_name or UNMATCHED, match.app_name


class ObservabilityMiddleware:
    """Registered first in MIDDLEWARE so timing covers Django's own security/session layers too."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if _is_excluded(request.path):
            # DjangoInstrumentor's excluded_urls only skips its own request
            # span - psycopg2/redis instrumentation still fires for readyz's
            # dependency checks otherwise, each becoming its own orphaned trace.
            with suppress_instrumentation():
                return self.get_response(request)

        method = request.method or ""
        route, tag = _resolve_route(request)
        # Cached for process_exception below - it fires later in the same
        # request/response cycle, and re-resolving from scratch there would
        # just repeat work already done here.
        request._observability_route = (route, tag)

        metrics.requests_in_progress.labels(method=method, route=route, tag=tag).inc()
        start = time.perf_counter()

        try:
            response = self.get_response(request)
        finally:
            metrics.requests_in_progress.labels(method=method, route=route, tag=tag).dec()

        duration = time.perf_counter() - start
        metrics.requests_duration_seconds.labels(method=method, route=route, tag=tag).observe(amount=duration)
        metrics.requests_total.labels(
            method=method,
            status_code=str(response.status_code),
            route=route,
            tag=tag,
        ).inc()

        return response

    def process_exception(self, request: HttpRequest, exception: Exception) -> None:
        """Django calls this for an unhandled exception raised by a view - the WSGI equivalent of an ASGI middleware's `except` branch."""
        if _is_excluded(request.path):
            return

        route, tag = getattr(request, "_observability_route", None) or _resolve_route(request)
        metrics.exceptions_total.labels(
            method=request.method or "",
            exception_type=type(exception).__name__,
            route=route,
            tag=tag,
        ).inc()
