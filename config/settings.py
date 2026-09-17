"""Django settings for the family_birthdays project."""

import os
from pathlib import Path

from celery.schedules import crontab
from dotenv import load_dotenv

from config.helpers import check_email_security_settings, get_env_bool, get_env_int, get_env_list

BASE_DIR = Path(__file__).resolve().parent.parent
if (env_path := BASE_DIR / ".env").exists():
    load_dotenv(env_path)

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
LOGIN_REDIRECT_URL = "family:dashboard"

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
# for Shabbat/Yom Tov notification-shift calculations.
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
# "console" just logs; swap in a real provider (Twilio, AWS SNS, ...) later
# by implementing it in notifications/services.py and pointing this at it.
SMS_BACKEND = os.getenv("SMS_BACKEND", "console")

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
