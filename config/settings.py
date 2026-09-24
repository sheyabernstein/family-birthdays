"""Django settings for the family_birthdays project."""

import os
from pathlib import Path

from celery.schedules import crontab
from dotenv import load_dotenv

from config.enums import TaskPriority
from config.helpers import (
    check_email_security_settings,
    get_env_bool,
    get_env_float,
    get_env_int,
    get_env_list,
)

BASE_DIR = Path(__file__).resolve().parent.parent
if (env_path := BASE_DIR / ".env").exists():
    load_dotenv(env_path)

BUILD_VERSION = os.getenv("BUILD_VERSION", "dev")
# Always the full commit sha, unlike BUILD_VERSION above (a tag name on a
# tagged release, else a short sha) - a tag is a mutable ref that can be
# force-moved or deleted later, so it's the wrong thing to link straight
# to source from (see family/templates/family/help.html) if the link is
# meant to stay valid forever. BUILD_VERSION is still what's shown -
# a tag reads far better than a bare sha in a Sentry release or a
# Prometheus build_info label - this is only for the link's own target.
BUILD_SHA = os.getenv("BUILD_SHA", "dev")

# Imported after load_dotenv() so its module-level logging setup reads
# LOG_LEVEL/etc from .env.
from config.logging_config import logger  # noqa: E402,F401

SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-change-me-in-.env")
DEBUG = get_env_bool("DEBUG", False)
ALLOWED_HOSTS = get_env_list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = get_env_list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_celery_results",
    "reversion",
    "accounts",
    "tenants",
    "family",
    "notifications",
]

# The scheme+host this app is served at, used to build absolute URLs with
# no request in play (a Celery-rendered email's "Manage notification
# settings" link - see notifications.services.absolute_url). A single
# "proto://fqdn" setting rather than a separate domain + use-https flag -
# one value to get right instead of two that have to agree, and no
# separate django.contrib.sites/DB row needed for something that's really
# just static per-deployment config, not admin-editable data - changing
# the real domain already means updating ALLOWED_HOSTS/DNS/TLS too, all of
# which need a redeploy regardless, so a DB row that could be edited
# independently bought nothing. Stored with no trailing slash so building
# a path onto it is always a plain concatenation.
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://localhost:8000").rstrip("/")

MIDDLEWARE = [
    # First, so request latency/in-flight metrics cover Django's own
    # security/session layers too, not just the "real" view - see
    # config/observability/django_middleware.py.
    "config.observability.django_middleware.ObservabilityMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tenants.middleware.CurrentFamilyMiddleware",
    # Wraps every request in a transaction and, if any registered model
    # was saved during it, records one Revision (who/when) covering all
    # of that request's changes - see reversion.register() calls on
    # Person/Union/Family/FamilyMembership/EventType/Mute/Account.
    "reversion.middleware.RevisionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.config",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database - built from discrete env vars rather than a DATABASE_URL parser,
# to keep the only config dependency being python-dotenv.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "family_birthdays"),
        "USER": os.getenv("POSTGRES_USER", "family_birthdays"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "family_birthdays"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": get_env_int("POSTGRES_PORT", 5432),
        "OPTIONS": {"sslmode": os.getenv("POSTGRES_SSLMODE", "prefer")},
    }
}

AUTH_USER_MODEL = "accounts.Account"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:request_link"
# Not actually read anywhere in this app - sign-in is fully custom
# (accounts.views.VerifyMagicLinkView), never django.contrib.auth.
# views.LoginView, the only built-in view that consults this setting.
# Kept accurate anyway in case that ever changes.
LOGIN_REDIRECT_URL = "family:home"

LANGUAGE_CODE = "en-us"

# The family's default timezone - used for celery beat send times and
# "today" calculations. Individual per-recipient timezones are a future
# enhancement; for now the whole family notification schedule runs on this.
TIME_ZONE = os.getenv("FAMILY_TIME_ZONE", "Europe/London")

USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

WHITENOISE_USE_FINDERS = DEBUG
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "config.storage.StableStaticFilesStorage"
        ),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Whether to use the two-day Diaspora Yom Tov calendar (vs. one-day Israel)
# for Shabbos/Yom Tov notification-shift calculations.
DIASPORA = get_env_bool("DIASPORA", True)

# --- Email ---
# One shared domain every family's own sender address is built from - see
# tenants.models.Family.sender_email (noreply-{slug}@this). Deliberately
# not SITE_BASE_URL (config/settings.py's own "the app is served at" value,
# used for building links/static URLs) - the two are legitimately
# different concerns (this app's own dev default, "localhost:8000", isn't
# even a valid email domain, and in production the sending domain is often
# its own subdomain so DKIM/SPF/DMARC reputation for mail stays separate
# from the web app's own domain). Verified at the domain level with SES,
# not per-address, so an arbitrary per-family local-part just works once
# this one domain is verified - no per-family provider setup needed.
EMAIL_SENDING_DOMAIN = os.getenv("EMAIL_SENDING_DOMAIN", "localhost")
DEFAULT_FROM_EMAIL = f"noreply@{EMAIL_SENDING_DOMAIN}"

_EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
_EMAIL_USE_TLS = get_env_bool("EMAIL_USE_TLS", True)
_EMAIL_USE_SSL = get_env_bool("EMAIL_USE_SSL", False)
check_email_security_settings(use_tls=_EMAIL_USE_TLS, use_ssl=_EMAIL_USE_SSL)
MAILERS = {
    "default": {
        "BACKEND": _EMAIL_BACKEND,
        "OPTIONS": (
            {
                "host": os.getenv("EMAIL_HOST", ""),
                "port": get_env_int("EMAIL_PORT", 587),
                "username": os.getenv("EMAIL_HOST_USER", ""),
                "password": os.getenv("EMAIL_HOST_PASSWORD", ""),
                # Both False is a real, working case (plain unencrypted
                # SMTP - e.g. a local smtp-proxy sidecar that already
                # terminates TLS to the real provider itself), not
                # something to guess a "sensible" default away from -
                # Django's own SMTP backend falls back to port 25 for
                # exactly this combination.
                "use_tls": _EMAIL_USE_TLS,
                "use_ssl": _EMAIL_USE_SSL,
            }
            if _EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
            else {}
        ),
    },
}

# --- SMS ---
# A dotted class path (see notifications/sms.py), same shape as
# STORAGES["staticfiles"]["BACKEND"] above - the default just logs; swap in
# a real provider by subclassing SmsBackend there and pointing this at it.
SMS_BACKEND = os.getenv("SMS_BACKEND", "notifications.sms.ConsoleSmsBackend")

# Only required when SMS_BACKEND is notifications.sms.SnsSmsBackend - see
# SnsSmsBackend.validate_settings(), called from NotificationsConfig.ready()
# rather than checked here (importing notifications.sms this early would
# mean it reads django.conf.settings while this very module is still
# mid-execution as the module that settings object resolves to). Passed
# explicitly to boto3 rather than relying on its own default credential
# chain (env vars/IAM role) so every SMS-relevant setting lives in one
# place, alongside SMS_BACKEND itself.
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_SNS_REGION = os.getenv("AWS_SNS_REGION", "")

# AWS's own Publish API throttle is a hard 10 req/s per account/region -
# an account-wide limit, not per-process, so a per-Celery-worker rate_limit
# can't enforce it correctly once more than one worker/replica exists (see
# notifications.sms's own Redis-backed counter, which checks this against
# every process regardless of how many are running). Kept below the real
# limit for headroom, not set to 10 itself.
SNS_PUBLISH_RATE_LIMIT_PER_SECOND = get_env_int("SNS_PUBLISH_RATE_LIMIT_PER_SECOND", 8)

# --- Redis --- (shared by Celery and the magic-link token store)
# Built from discrete env vars rather than accepting a REDIS_URL directly -
# same reasoning as DATABASES above, mirroring the POSTGRES_* pattern.
# REDIS_PASSWORD is genuinely optional (blank locally) - the auth segment
# is only included in the built URL when a password is actually set, since
# an empty `:@` userinfo segment would make redis-py attempt an (incorrect)
# empty-password AUTH against a server that has none configured at all.
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = get_env_int("REDIS_PORT", 6379)
REDIS_DB = get_env_int("REDIS_DB", 0)
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_URL = (
    f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    if REDIS_PASSWORD
    else f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
)

# --- Celery ---
CELERY_BROKER_URL = REDIS_URL
# Stored in Postgres (via django-celery-results), not Redis, so a
# TaskResult row survives a broker restart and shows up in the admin -
# see notifications.admin and the "Task results" link there for owners.
CELERY_RESULT_BACKEND = "django-db"
# Without this, TaskResult rows record status/traceback but not which task
# or with what args - useless for the admin's execution log.
CELERY_RESULT_EXTENDED = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
# How long a TaskResult row (django-celery-results) sticks around before
# celery beat's automatic "celery.backend_cleanup" task deletes it.
CELERY_TASK_RESULT_EXPIRES = get_env_int("CELERY_TASK_RESULT_EXPIRES", 60 * 60 * 24 * 30)  # 30 days

# RedBeat keeps the schedule in Redis instead of a DB table, which is what
# lets beat run embedded inside a worker process (`celery worker -B`, see
# docker-compose.yml - this setting alone selects RedBeat, no -S/--scheduler
# flag needed) instead of needing its own dedicated container - RedBeat uses
# a Redis lock so it's still safe if more than one worker replica has -B
# set. There's no per-family or
# admin-editable schedule requirement here, so the static dict below (not
# a DB-backed one, like django_celery_beat's DatabaseScheduler would want)
# is all this needs.
CELERY_BEAT_SCHEDULER = "redbeat.RedBeatScheduler"
REDBEAT_REDIS_URL = REDIS_URL
# RedBeat currently falls back to CELERY_BROKER_TRANSPORT_OPTIONS
# (queue_order_strategy, below - a kombu/broker-only option, not a real
# Redis client kwarg) when this isn't set explicitly, logging a
# deprecation warning every time beat starts - confirmed directly
# against the installed redbeat package that this fallback value is
# silently discarded anyway (it isn't a recognized redis-py connection
# option), so an explicit empty dict here is a no-op today and avoids a
# hard break once redbeat 2.5.0 drops the fallback.
REDBEAT_REDIS_OPTIONS = {}

# Task priority is three real Celery queues (high/normal/low - see
# config.enums.TaskPriority), consumed by the single worker process in
# that fixed order, not kombu's own per-message Redis "priority" emulation
# (Task.apply_async's priority= kwarg / broker_transport_options'
# priority_steps). That emulation was tried first and hit a live kombu
# 5.6.2 bug: the moment any task actually carried a non-zero priority, the
# worker's own pidbox control-command replies (always priority 0) started
# throwing inside kombu's exchange lookup, and the worker silently stopped
# consuming the priority-suffixed queue afterward until restarted -
# reproduced directly against a real docker stack. queue_order_strategy=
# "priority" never touches that code path at all - kombu's own docs
# describe it plainly: "Consume from queues in original order, so that if
# the first queue always contains messages, the rest of the queues in the
# list will never be consumed from." See config.enums.TaskPriority's own
# docstring and docker/entrypoints/worker.sh's `-Q high,normal,low` (order
# matters - it's what actually gives high its priority here).
# worker_prefetch_multiplier=1 is still required: otherwise the worker
# prefetches a batch of low-priority tasks before a high-priority one
# (e.g. a magic-link send) ever gets a chance to jump the line, silently
# defeating the whole point of separate queues.
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "queue_order_strategy": "priority",
}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = TaskPriority.NORMAL

CELERY_BEAT_SCHEDULE = {
    "compute-occurrences-nightly": {
        "task": "notifications.tasks.compute_occurrences",
        "schedule": crontab(hour=2, minute=0),
    },
    "send-due-notifications-morning": {
        "task": "notifications.tasks.send_due_notifications",
        "schedule": crontab(hour=7, minute=0),
    },
    "send-due-broadcasts": {
        "task": "notifications.tasks.send_due_broadcasts",
        "schedule": crontab(minute="*/5"),
    },
}

# --- Observability ---
# OTel is always on (spans are always recorded); a real OTLP exporter
# (Tempo in prod) is only attached once OTEL_ENDPOINT is actually set -
# OTEL_ENABLED isn't its own env var, since "is there an endpoint to
# export to" already answers the same question and a separate flag would
# just be a second setting that could disagree with the first (e.g.
# OTEL_ENABLED=true with no OTEL_ENDPOINT, or vice versa). Same reasoning
# for SENTRY_ENABLED/SENTRY_DSN below. See config/observability/ for how
# these are actually used - kept here, not read via bare os.getenv() in
# that package, so every env var this app parses goes through one place,
# per this file's own existing convention.
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "family-birthdays")
OTEL_ENDPOINT = os.getenv("OTEL_ENDPOINT", "")
OTEL_ENABLED = bool(OTEL_ENDPOINT)
OTEL_TRACES_SAMPLE_RATE = get_env_float("OTEL_TRACES_SAMPLE_RATE", 1.0)
# k=v pairs, e.g. "authorization=Bearer xyz" - for an OTLP collector that
# needs auth headers; blank/unset sends no extra headers.
OTEL_EXPORTER_OTLP_HEADERS = get_env_list("OTEL_EXPORTER_OTLP_HEADERS", default=[])

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_ENABLED = bool(SENTRY_DSN)
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "development")
# Off switch for trace *volume* only, not error reporting - an exception
# still reaches Sentry either way (config/observability/sentry.py's own
# OTelSpan.record_exception patch is a separate code path from trace
# mirroring). Defaults on to match this app's existing behavior for
# anyone not setting it. See that module's own SentrySpanProcessor
# comment for the one real cost of turning this off: a Sentry issue can
# no longer be cross-referenced to its matching Tempo trace by id, since
# that link is populated by the same object that mirrors spans.
SENTRY_TRACES_ENABLED = get_env_bool("SENTRY_TRACES_ENABLED", default=True)

# Prometheus namespace prefix for every metric this app exports (see
# config/observability/metrics.py) and the multiprocess directory
# gunicorn/Celery's prefork workers write per-pid metric files into (see
# config/observability/multiproc.py, config/gunicorn_conf.py's post_fork
# hook, and config/celery.py's worker_process_init receiver).
METRICS_NAMESPACE = os.getenv("METRICS_NAMESPACE", "family_birthdays")
PROMETHEUS_MULTIPROC_DIR = os.getenv("PROMETHEUS_MULTIPROC_DIR", "/tmp/prom_multiproc")

# --- Logging (structlog) ---
# JSON always (not just in production) - one consistent shape for every
# log line, including Django's and Celery's own. See config/logging_config.py
# (imported above, right after load_dotenv()) - every module imports its
# `logger` from there instead of calling structlog.get_logger(__name__)
# itself.

# django.setup() always calls django.utils.log.configure_logging() right
# after this settings module finishes importing, and by default that
# re-applies Django's own DEFAULT_LOGGING via logging.config.dictConfig() on
# top of what config/logging_config.py just set up - silently reverting the
# "django" logger (and so django.request, which isn't named in Django's own
# default and just inherits from it) back to a plain, unformatted
# StreamHandler. Setting this to None is Django's documented way to say
# "logging is configured elsewhere" and skips that step entirely.
LOGGING_CONFIG = None
