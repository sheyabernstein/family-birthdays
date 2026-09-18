"""Sentry error tracking setup.

The OTel TracerProvider (config/observability/tracing.py) is always live,
regardless of whether Tempo export is configured, so its spans are always
recording - attributes, exceptions, etc. are always captured. Sentry's own
Django/Celery auto-instrumentation would otherwise start its own root
transaction (its own trace/span ids) on every request/task, competing with
that shared span; that auto-tracing is disabled here and Sentry instead
mirrors the *same* OTel spans via `SentrySpanProcessor`. That's what makes
trace/span ids - and every log line's own trace_id/span_id (see
config/logging_config.py) - match across Tempo, Sentry, and logs, in every
deployment case (OTel only, Sentry only, both). Ported from a FastAPI
service (prom-gateway) built with the identical pattern.
"""

import os

import sentry_sdk
from django.conf import settings
from opentelemetry import trace as otel_trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.trace import Span as OTelSpan
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.opentelemetry import SentryPropagator, SentrySpanProcessor

from config.exceptions import FamilyBirthdaysError
from config.logging_config import logger

# Any FamilyBirthdaysError subclass (config/exceptions.py) is this app's
# own deliberate, already-logged domain error - e.g. SmsUnrecoverableError
# (notifications/sms.py), a permanent send failure notifications.tasks.
# send_message re-raises on purpose so Celery's own result backend records
# the real FAILURE state, not a bug worth alerting on. Checked against the
# base class, not a hardcoded tuple of specific exceptions, so a future
# domain error automatically gets the same treatment just by inheriting
# from it - the same "already-handled outcome, not a bug" reasoning
# prom-gateway's own sentry.py applies to an already-handled HTTPException.
# A genuinely unexpected failure should never subclass this - only
# something that's already logged and handled belongs here.
_original_record_exception = OTelSpan.record_exception


def _record_exception_and_capture(self: OTelSpan, exception: BaseException, *args, **kwargs) -> None:
    if not isinstance(exception, FamilyBirthdaysError):
        sentry_sdk.capture_exception(exception)
    _original_record_exception(self, exception, *args, **kwargs)


_initialized = False


def init_sentry() -> None:
    """Initializes Sentry, if enabled. Must run after `trace.set_tracer_provider`."""
    global _initialized
    if _initialized:
        return

    if not settings.SENTRY_ENABLED:
        logger.debug("sentry is not enabled, skipping init")
        return

    build_version = os.getenv("BUILD_VERSION", "dev")

    logger.info(
        "enabling sentry",
        environment=settings.SENTRY_ENVIRONMENT,
        release=build_version,
    )
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        release=build_version,
        instrumenter="otel",
        # Not a sampling knob - sampling already happened at the OTel
        # TracerProvider (OTEL_TRACES_SAMPLE_RATE); a dropped OTel span
        # never reaches SentrySpanProcessor at all. This just flips on
        # Sentry's own tracing subsystem, which otherwise makes
        # start_transaction() (called from SentrySpanProcessor.on_start) a
        # no-op that sends nothing.
        traces_sample_rate=1.0,
        disabled_integrations=[DjangoIntegration(), CeleryIntegration()],
        # Keep log breadcrumbs, but don't let error-level log calls create
        # their own Sentry issues independently of
        # _record_exception_and_capture below - most of those log calls sit
        # right next to a raise that would otherwise report the same
        # failure twice (structlog is this app's own logging pipeline, see
        # config/logging_config.py).
        integrations=[LoggingIntegration(event_level=None)],
    )

    # Mirrors spans from the shared TracerProvider into Sentry, with the
    # same trace/span ids and attributes. Also propagates Sentry's
    # distributed-tracing headers over the same OTel context.
    otel_trace.get_tracer_provider().add_span_processor(SentrySpanProcessor())
    set_global_textmap(SentryPropagator())

    # OTel's Django/Celery instrumentation records unhandled exceptions
    # onto the active span via `span.record_exception`; SentrySpanProcessor
    # doesn't forward those to Sentry on its own, so patch it here to
    # capture them too.
    OTelSpan.record_exception = _record_exception_and_capture

    _initialized = True
