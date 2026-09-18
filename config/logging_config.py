"""Structured logging setup - JSON always, one shared logger.

Every module imports `logger` from here rather than calling
structlog.get_logger(__name__) itself, so there's a single place that
owns the processor chain and a single object everywhere else just uses.
Standard-library logging (Django's own request logs, Celery's) is routed
through the same JSON pipeline via ProcessorFormatter, so container logs
are one consistent shape regardless of who emitted them.

Configuration runs as a side effect of importing this module, not behind
a function call - every caller reads the same env vars, so there was
nothing left for arguments to vary. `config/settings.py` imports this
after `load_dotenv()` so `.env` is already in `os.environ`;
`config/gunicorn_conf.py` and standalone scripts (e.g.
`docker/scripts/wait_for_postgres.py`) import `LOGGING_DICT_CONFIG`/
`logger` directly, since they run before `config.settings` is available.
"""

import json
import logging.config
import os
import sys
import traceback
from typing import Any

import structlog
from opentelemetry import trace as otel_trace
from structlog.typing import WrappedLogger


class OrderedJSONRenderer:
    """Render the event dict as JSON with level/event first, then sorted keys."""

    def __call__(self, logger: WrappedLogger, name: str, event_dict: dict[str, Any]) -> str:
        level = event_dict.pop("level", None)
        event = event_dict.pop("event", None)

        ordered = {}
        if level is not None:
            ordered["level"] = level
        if event is not None:
            ordered["event"] = event
        ordered.update(sorted(event_dict.items()))

        return json.dumps(ordered, default=str)


def add_trace_context(logger: WrappedLogger, name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Injects the active OTel span's trace_id/span_id into every log line.

    `get_current_span()` always returns a valid object - a real recording
    span if one is active, otherwise a cheap no-op INVALID_SPAN - so this
    is safe to call unconditionally, including before
    config.observability.tracing.init_tracing() has run (e.g. a bare
    manage.py command) or entirely outside a request/task (nothing gets
    added, event_dict passes through unchanged). This is what actually
    makes the trace/span ids in Tempo/Sentry match what shows up in these
    JSON log lines - see config/observability/sentry.py's own docstring
    for why that match matters.
    """
    span_context = otel_trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")
    return event_dict


def render_exception(logger: WrappedLogger, name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Turn `exc_info` into a structured, nested `exception` key.

    Deliberately not `structlog.processors.format_exc_info` (a flat string -
    hard to filter/query on) or `structlog.processors.dict_tracebacks` (a
    full per-frame local-variable dump - a real leak risk here, since a
    frame's locals routinely include message bodies, tokens, or the
    destination address). This keeps only what's actually useful to filter
    or read: the exception's own type/message/raise-site, plus the call
    chain that led there, with no source text and no locals.

    Our own call sites always pass `exc_info=exc` (the exception instance)
    rather than `exc_info=True` - the instance carries its own
    `__traceback__`, so this works even when the exception is logged well
    after its `except` block closed (e.g.
    `docker/scripts/wait_for_postgres.py`, which catches, returns, and only
    logs several retries later). `exc_info=True`/a `sys.exc_info()` tuple is
    still handled here too, since Django's/Celery's own logging (routed
    through this same `foreign_pre_chain`) uses that form, not ours.
    """
    exc_info = event_dict.pop("exc_info", None)
    if not exc_info:
        return event_dict

    if isinstance(exc_info, BaseException):
        exc = exc_info
    else:
        _, exc, _ = sys.exc_info() if exc_info is True else exc_info
    if exc is None:
        return event_dict

    frames = traceback.extract_tb(exc.__traceback__)
    raise_site = frames[-1] if frames else None

    event_dict["exception"] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "file": raise_site.filename if raise_site else None,
        "line": raise_site.lineno if raise_site else None,
        "stack": [{"file": frame.filename, "line": frame.lineno, "function": frame.name} for frame in frames],
    }
    return event_dict


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DJANGO_LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "WARNING")
CELERY_LOG_LEVEL = os.getenv("CELERY_LOG_LEVEL", "INFO")

# The stdlib `logging.config.dictConfig()` dict this app uses everywhere -
# also handed straight to gunicorn's own `logconfig_dict` setting, see
# `config/gunicorn_conf.py`.
LOGGING_DICT_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": OrderedJSONRenderer(),
            "foreign_pre_chain": [
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.StackInfoRenderer(),
                add_trace_context,
                render_exception,
            ],
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {"level": LOG_LEVEL, "handlers": ["console"]},
    "loggers": {
        # Celery is chatty at INFO about task plumbing; keep it quieter
        # unless someone's actively debugging worker behavior.
        "django": {"level": DJANGO_LOG_LEVEL, "handlers": ["console"], "propagate": False},
        "celery": {"level": CELERY_LOG_LEVEL, "handlers": ["console"], "propagate": False},
        # Third-party client libraries - their own DEBUG/INFO chatter (e.g.
        # redis-py's per-connection protocol negotiation) isn't actionable
        # here, so these stay quiet regardless of our own LOG_LEVEL. Add
        # another entry here, not a broad LOG_LEVEL bump, the next time a
        # new dependency turns out to be noisy.
        "django.request": {"level": "ERROR", "handlers": ["console"], "propagate": False},
        "gunicorn.error": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "gunicorn.access": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "redis": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "kombu": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "opentelemetry": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "urllib3": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
}

logging.config.dictConfig(LOGGING_DICT_CONFIG)

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        add_trace_context,
        render_exception,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(LOG_LEVEL)),
    cache_logger_on_first_use=True,
)

# The one shared logger - every module imports this instead of calling
# structlog.get_logger(__name__) itself.
logger = structlog.get_logger("family_birthdays")
