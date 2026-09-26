from django.test import RequestFactory
from opentelemetry.context import get_current
from opentelemetry.instrumentation.utils import _SUPPRESS_INSTRUMENTATION_KEY

from config.observability import metrics
from config.observability.django_middleware import UNMATCHED, ObservabilityMiddleware
from config.tests.conftest import sample_value


def _middleware(get_response):
    return ObservabilityMiddleware(get_response)


def test_excluded_path_suppresses_instrumentation_for_the_view():
    seen = {}

    def get_response(request):
        seen["suppressed"] = get_current().get(_SUPPRESS_INSTRUMENTATION_KEY)
        return _response(200)

    _middleware(get_response)(RequestFactory().get("/readyz"))

    assert seen["suppressed"] is True


def test_excluded_path_skips_all_metrics_on_the_way_in():
    calls = []

    def get_response(request):
        calls.append(request)
        return _response(200)

    request = RequestFactory().get("/healthz")
    before = sample_value(metrics.requests_in_progress, method="GET", route=UNMATCHED, tag="")

    _middleware(get_response)(request)

    assert calls  # the view still ran
    assert sample_value(metrics.requests_in_progress, method="GET", route=UNMATCHED, tag="") == before


def test_excluded_path_skips_process_exception_too():
    middleware = _middleware(lambda request: _response(200))
    request = RequestFactory().get("/readyz")
    before = sample_value(
        metrics.exceptions_total, method="GET", exception_type="ValueError", route="", tag=""
    )

    middleware.process_exception(request, ValueError("boom"))

    assert (
        sample_value(metrics.exceptions_total, method="GET", exception_type="ValueError", route="", tag="")
        == before
    )


def test_unresolvable_path_is_labeled_unmatched():
    middleware = _middleware(lambda request: _response(200))
    request = RequestFactory().get("/this/path/does/not/exist/")

    response = middleware(request)

    assert response.status_code == 200
    route, tag = request._observability_route
    assert route == UNMATCHED
    assert tag == ""


def test_a_real_request_records_total_duration_and_in_progress():
    request = RequestFactory().get("/accounts/login/")
    before_total = sample_value(
        metrics.requests_total, method="GET", status_code="200", route="request_link", tag="accounts"
    )

    response = _middleware(lambda req: _response(200))(request)

    assert response.status_code == 200
    assert (
        sample_value(
            metrics.requests_total, method="GET", status_code="200", route="request_link", tag="accounts"
        )
        == before_total + 1
    )
    assert sample_value(metrics.requests_in_progress, method="GET", route="request_link", tag="accounts") == 0


def test_process_exception_uses_the_cached_route_from_the_request():
    """Avoids re-resolving the URL a second time - the request already
    carries _observability_route from __call__ earlier in the same
    request/response cycle."""
    middleware = _middleware(lambda req: _response(200))
    request = RequestFactory().get("/accounts/login/")
    middleware(request)  # populates request._observability_route
    before = sample_value(
        metrics.exceptions_total,
        method="GET",
        exception_type="ValueError",
        route="request_link",
        tag="accounts",
    )

    middleware.process_exception(request, ValueError("boom"))

    assert (
        sample_value(
            metrics.exceptions_total,
            method="GET",
            exception_type="ValueError",
            route="request_link",
            tag="accounts",
        )
        == before + 1
    )


def _response(status_code: int):
    from django.http import HttpResponse

    return HttpResponse(status=status_code)
