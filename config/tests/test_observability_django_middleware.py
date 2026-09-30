import pytest
from django.test import RequestFactory
from opentelemetry.context import get_current
from opentelemetry.instrumentation.utils import _SUPPRESS_INSTRUMENTATION_KEY
from opentelemetry.util.http import parse_excluded_urls

from config.observability import metrics
from config.observability.django_middleware import EXCLUDED_URLS_PATTERN, UNMATCHED, ObservabilityMiddleware
from config.tests.conftest import sample_value


def _middleware(get_response):
    return ObservabilityMiddleware(get_response)


@pytest.mark.parametrize(
    ["absolute_uri", "expected"],
    [
        ["http://testserver/healthz?", True],
        ["http://testserver/readyz?", True],
        ["http://testserver/static/css/app.css?", True],
        ["http://testserver/media/x.jpg?", True],
        ["http://testserver/foo/healthz?", False],
        ["http://testserver/healthzabc?", False],
        ["http://testserver/accounts/login/?", False],
        ["http://testserver/?", False],
    ],
    ids=[
        "healthz is excluded",
        "readyz is excluded",
        "a real static asset is excluded",
        "a real media asset is excluded",
        "healthz as a later path segment is not excluded",
        "a path merely starting with healthz is not excluded",
        "an ordinary route is not excluded",
        "the root path is not excluded",
    ],
)
def test_excluded_urls_pattern_matches_the_full_absolute_uri_django_instrumentor_actually_checks(
    absolute_uri, expected
):
    # EXCLUDED_URLS_PATTERN feeds DjangoInstrumentor's own excluded_urls
    # directly (config.observability.tracing) - verified live against a
    # running container that opentelemetry-instrumentation-django's own
    # otel_middleware.py calls url_disabled() with request.build_
    # absolute_uri("?") (a full "scheme://host/path?" URL), never the
    # bare path _is_excluded below matches against. An earlier, path-
    # anchored version of this pattern silently matched nothing at all
    # here - every "excluded" request still got a real trace. This test
    # goes through parse_excluded_urls, the exact same helper
    # DjangoInstrumentor itself calls, rather than re-deriving the
    # match logic independently.
    excluded = parse_excluded_urls(EXCLUDED_URLS_PATTERN)

    assert excluded.url_disabled(absolute_uri) is expected


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


def test_static_prefix_suppresses_instrumentation_for_the_view():
    seen = {}

    def get_response(request):
        seen["suppressed"] = get_current().get(_SUPPRESS_INSTRUMENTATION_KEY)
        return _response(200)

    _middleware(get_response)(RequestFactory().get("/static/css/app.css"))

    assert seen["suppressed"] is True


def test_static_prefix_skips_all_metrics_on_the_way_in():
    # requests_total, not requests_in_progress - the in-progress gauge
    # nets back to the same value either way (inc then dec, whether
    # excluded or not), so it can't actually distinguish "skipped" from
    # "ran normally and cancelled out". requests_total only increments on
    # the non-excluded path, so it's the one that'd actually catch a
    # regression here.
    calls = []

    def get_response(request):
        calls.append(request)
        return _response(200)

    request = RequestFactory().get("/static/icons/sprite.svg")
    before = sample_value(metrics.requests_total, method="GET", status_code="200", route=UNMATCHED, tag="")

    _middleware(get_response)(request)

    assert calls  # the view still ran
    assert (
        sample_value(metrics.requests_total, method="GET", status_code="200", route=UNMATCHED, tag="")
        == before
    )


def test_media_prefix_skips_all_metrics_on_the_way_in():
    # MEDIA_URL isn't backed by any real feature yet - this just confirms
    # the exclusion is already wired up for whenever it is.
    calls = []

    def get_response(request):
        calls.append(request)
        return _response(200)

    request = RequestFactory().get("/media/whatever.jpg")
    before = sample_value(metrics.requests_total, method="GET", status_code="200", route=UNMATCHED, tag="")

    _middleware(get_response)(request)

    assert calls
    assert (
        sample_value(metrics.requests_total, method="GET", status_code="200", route=UNMATCHED, tag="")
        == before
    )


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


def test_excluded_path_match_is_exact_not_a_substring():
    # A path that merely contains "/healthz" shouldn't be excluded - only
    # the real health-check path itself. Regression guard for the same
    # anchoring the exported EXCLUDED_URLS_PATTERN relies on.
    calls = []

    def get_response(request):
        calls.append(request)
        return _response(200)

    request = RequestFactory().get("/foo/healthz")
    before = sample_value(metrics.requests_total, method="GET", status_code="200", route=UNMATCHED, tag="")

    _middleware(get_response)(request)

    assert calls
    assert (
        sample_value(metrics.requests_total, method="GET", status_code="200", route=UNMATCHED, tag="")
        == before + 1
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
