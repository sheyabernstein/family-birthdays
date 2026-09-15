"""Settings for pytest.

Swaps Postgres/Redis for in-process fakes so the whole suite runs with
no docker services up. See tests/conftest.py for the fakeredis patch
that goes with the CELERY/broker changes here.
"""

from config.settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# No Postgres-specific fields/constraints are in use, so sqlite is a faithful
# stand-in for tests and lets the suite run without docker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
