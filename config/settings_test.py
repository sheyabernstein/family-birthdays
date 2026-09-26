"""Settings for pytest.

Swaps Postgres/Redis for in-process fakes so the whole suite runs with
no docker services up.
"""

from fakeredis import FakeRedisConnection

from config.settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Same django-redis backend as config/settings.py (needed for real
# cache.lock()/get_redis_connection() behavior in tests - see
# tenants/management/commands/migrate_with_lock.py and accounts/
# magic_links.py), but every connection it opens is actually fakeredis's
# in-process FakeRedisConnection instead of a real socket to Redis - this
# is fakeredis's own documented recipe for django-redis
# (CONNECTION_POOL_KWARGS), same "swap the real backing service for an
# in-process fake" as DATABASES above. The `lua` extra (pyproject.toml)
# is required, not optional - redis-py's own Lock.release() (used by
# migrate_with_lock.py's cache.lock()) is an EVALSHA'd Lua script, and
# fakeredis only emulates EVAL/EVALSHA with that extra installed;
# confirmed directly - without it, release() fails with a `ResponseError:
# unknown command 'evalsha'`. conftest.py's autouse _clear_cache fixture
# resets the cache between tests, since it's one process-wide fake store
# for the whole suite rather than a fresh connection per test.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,  # noqa: F405
        "OPTIONS": {"CONNECTION_POOL_KWARGS": {"connection_class": FakeRedisConnection}},
    }
}

# No Postgres-specific fields/constraints are in use, so sqlite is a faithful
# stand-in for tests and lets the suite run without docker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
