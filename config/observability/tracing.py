"""OpenTelemetry TracerProvider setup - always on, vendor-neutral.

The TracerProvider is always live, regardless of whether an OTLP exporter
(Tempo) is configured, so spans are always recorded - attributes,
exceptions, etc. are always captured. Tempo and Sentry (see
config/observability/sentry.py) are just optional exporters attached to
this one provider, so a single sampler governs volume for both and every
backend sees the same trace/span ids. Mirrors the pattern used in
prom-gateway, a FastAPI service built the same way.

Call sites, deliberately not config/settings.py itself: `DjangoInstrumentor
().instrument()` (below) mutates `django.conf.settings.MIDDLEWARE` - doing
that from inside settings.py while it's still mid-import is the kind of
circular-import fragility that's easy to get subtly wrong, and isn't how
OTel's own Django docs recommend wiring it up anyway. `config/wsgi.py`
calls `init_tracing()` once, after `get_wsgi_application()` has fully
initialized Django (covers gunicorn's WSGI workers - gunicorn forks
*before* `config.wsgi` is even imported by each worker, so no separate
post-fork re-init is needed on the web side, unlike Celery below).
`config/celery.py`'s `worker_process_init` receiver calls it again for the
same reason Celery needs its own post-fork re-init: Celery's prefork pool
forks worker child processes *after* `config.celery` (and everything it
imports, including this module) is first imported in the parent - a
BatchSpanProcessor's background export thread does not survive fork()
correctly, so `init_tracing()` must run again post-fork, in the child.
`init_tracing()` is idempotent (guarded below), so calling it more than
once in the same process is a no-op past the first call. Bare `manage.py`
management commands (migrations, shell, etc.) never call this - a one-off
command doesn't need request/task tracing.
"""

from django.conf import settings
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from config.logging_config import logger
from config.observability.django_middleware import EXCLUDED_PATHS

_PROVIDER: TracerProvider | None = None


def _normalized_headers() -> dict[str, str]:
    """Parse OTLP header pairs into a normalized dictionary."""
    headers = {}

    for pair in settings.OTEL_EXPORTER_OTLP_HEADERS:
        key, separator, value = pair.partition("=")
        if not separator:
            continue

        key, value = key.strip(), value.strip()
        if key and value:
            headers[key.lower()] = value

    return headers


def init_tracing() -> TracerProvider:
    """Builds (once per process) or returns the shared TracerProvider.

    Also instruments Django/Celery/Redis/psycopg2/botocore - each
    instrumentor is itself idempotent (calling .instrument() twice is a
    documented no-op), so re-calling this post-fork in a Celery child is
    safe even though the parent already called it once at import time.
    """
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER

    resource = Resource(attributes={SERVICE_NAME: settings.OTEL_SERVICE_NAME})
    sampler = ParentBased(TraceIdRatioBased(settings.OTEL_TRACES_SAMPLE_RATE))
    provider = TracerProvider(resource=resource, sampler=sampler)

    if settings.OTEL_ENABLED:
        logger.info("enabling otel exporter", endpoint=settings.OTEL_ENDPOINT)
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.OTEL_ENDPOINT, headers=_normalized_headers())
            )
        )

    trace.set_tracer_provider(provider)
    _PROVIDER = provider

    from config.observability.sentry import init_sentry

    init_sentry()

    # Same excluded paths as our own metrics middleware - without this,
    # Docker's own healthcheck (config/urls.py's /readyz, hit every 10s)
    # generates a new span/trace forever, and since Sentry mirrors spans
    # off this same TracerProvider (config/observability/sentry.py), that
    # noise reaches both Tempo and Sentry identically, not just one of them.
    DjangoInstrumentor().instrument(excluded_urls=",".join(EXCLUDED_PATHS))
    CeleryInstrumentor().instrument()
    RedisInstrumentor().instrument()
    Psycopg2Instrumentor().instrument()
    BotocoreInstrumentor().instrument()

    return provider


def flush_tracing(timeout_ms: int = 5_000) -> None:
    """Flushes any buffered spans - call on graceful shutdown (WSGI worker exit, Celery worker shutdown)."""
    if _PROVIDER is not None:
        _PROVIDER.force_flush(timeout_millis=timeout_ms)
