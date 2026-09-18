"""Prometheus metric definitions.

Importing this module triggers metric registration against the default
global registry - `config.observability.multiproc.init_multiprocess_dir()`
must have already run (PROMETHEUS_MULTIPROC_DIR set) before this import,
same requirement as prom-gateway's equivalent module.
"""

from django.conf import settings
from prometheus_client import Counter, Gauge, Histogram

METRICS_NAMESPACE = settings.METRICS_NAMESPACE

# ---------------------------------------------------------------------------
# Histogram bucket definitions
# ---------------------------------------------------------------------------

_HTTP_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
    float("inf"),
)

# Celery tasks in this app range from sub-second (send_message) to tens of
# seconds (compute_occurrences sweeping every family's people/unions).
_TASK_BUCKETS = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    float("inf"),
)


# ---------------------------------------------------------------------------
# HTTP request metrics (populated by config.observability.django_middleware)
# ---------------------------------------------------------------------------

# route/tag only, deliberately never the raw request path - route is the
# resolved URL pattern's own name (e.g. "person_detail"), never the actual
# path with a captured value filled in, so a per-person/union/etc UUID in
# the URL (see AGENTS.md's own "every model gets a uuid" convention) never
# turns into its own unbounded metric series. tag is the URL's app_name -
# both are drawn from a small, fixed set of registered URL patterns/apps,
# not from request data.

requests_total = Counter(
    name="requests_total",
    documentation="Total HTTP requests handled, observed at response completion.",
    labelnames=("method", "status_code", "route", "tag"),
    namespace=METRICS_NAMESPACE,
)

requests_duration_seconds = Histogram(
    name="requests_duration_seconds",
    documentation="HTTP request latency in seconds, observed at response completion.",
    labelnames=("method", "route", "tag"),
    namespace=METRICS_NAMESPACE,
    buckets=_HTTP_BUCKETS,
)

requests_in_progress = Gauge(
    name="requests_in_progress",
    documentation="Number of in-flight HTTP requests currently being handled.",
    labelnames=("method", "route", "tag"),
    namespace=METRICS_NAMESPACE,
    multiprocess_mode="livesum",
)

exceptions_total = Counter(
    name="exceptions_total",
    documentation="Total unhandled exceptions raised while processing a request.",
    labelnames=("method", "exception_type", "route", "tag"),
    namespace=METRICS_NAMESPACE,
)


# ---------------------------------------------------------------------------
# Celery task metrics (populated by config.observability.celery_signals)
# ---------------------------------------------------------------------------

celery_tasks_total = Counter(
    name="celery_tasks_total",
    documentation="Total Celery tasks completed, by outcome.",
    labelnames=("task_name", "status"),
    namespace=METRICS_NAMESPACE,
)

celery_task_duration_seconds = Histogram(
    name="celery_task_duration_seconds",
    documentation="Celery task run time in seconds, by outcome.",
    labelnames=("task_name", "status"),
    namespace=METRICS_NAMESPACE,
    buckets=_TASK_BUCKETS,
)

celery_tasks_in_progress = Gauge(
    name="celery_tasks_in_progress",
    documentation="Number of Celery tasks currently executing.",
    labelnames=("task_name",),
    namespace=METRICS_NAMESPACE,
    multiprocess_mode="livesum",
)


# ---------------------------------------------------------------------------
# Business metrics - notification volume, for cost visibility over time.
# ---------------------------------------------------------------------------

# event_type is always one of notifications.models.EventType.BuiltinCode's
# values, "custom" (any family-defined event type), or "magic_link" (the
# sign-in flow, which calls send_email/send_sms directly, bypassing Message
# entirely) - never a raw, family-controlled EventType.code, which would
# make this label's cardinality scale with every family's own custom event
# types for a metric whose whole point is aggregate volume/cost tracking.
notifications_emails_sent_total = Counter(
    name="notifications_emails_sent_total",
    documentation="Total emails sent, by outcome and event type.",
    labelnames=("status", "event_type"),
    namespace=METRICS_NAMESPACE,
)

notifications_sms_sent_total = Counter(
    name="notifications_sms_sent_total",
    documentation="Total SMS sent, by outcome, event type, and destination country.",
    labelnames=("status", "event_type", "country"),
    namespace=METRICS_NAMESPACE,
)

# Set directly inside compute_occurrences/send_due_notifications/
# send_due_broadcasts (notifications/tasks.py) at the end of each function
# body - deliberately not derived from a generic "did this Celery task run"
# signal. These three tasks are designed to silently self-heal (idempotent
# recompute, send_date__lte catch-up, RedBeat's leader lock), so "ran on
# schedule" isn't the same as "did its job" - a signal-based gauge can't
# tell a genuine failure apart from an expected missed-and-caught-up tick,
# but a gauge set at the end of a successful function body naturally can.
beat_task_last_success_timestamp = Gauge(
    name="beat_task_last_success_timestamp",
    documentation="Unix timestamp of the last successful completion of a scheduled task.",
    labelnames=("task_name",),
    namespace=METRICS_NAMESPACE,
    multiprocess_mode="max",
)


# ---------------------------------------------------------------------------
# Build info (set once per process at startup)
# ---------------------------------------------------------------------------

build_info = Gauge(
    name="build_info",
    documentation="Build info for the running process. Always 1; the version is in the label.",
    namespace=METRICS_NAMESPACE,
    labelnames=("version",),
    multiprocess_mode="max",
)


def set_build_info(version: str) -> None:
    """Records build info. Call once per process at startup."""
    build_info.labels(version=version).set(1)
