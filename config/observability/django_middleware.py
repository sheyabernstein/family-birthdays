"""Prometheus HTTP request metrics middleware.

The WSGI/sync equivalent of prom-gateway's ASGI metrics middleware: wraps
each request to track in-flight count, latency, completion count, and
unhandled exceptions.
"""

import time
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.urls import Resolver404, resolve

from config.observability import metrics

EXCLUDED_PATHS = frozenset({"/healthz", "/readyz"})
UNMATCHED = "unmatched"


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
        if request.path in EXCLUDED_PATHS:
            return self.get_response(request)

        method = request.method or ""
        route, tag = _resolve_route(request)

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
        if request.path in EXCLUDED_PATHS:
            return

        route, tag = _resolve_route(request)
        metrics.exceptions_total.labels(
            method=request.method or "",
            exception_type=type(exception).__name__,
            route=route,
            tag=tag,
        ).inc()
