import socket
import uuid
from typing import Any

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django_redis import get_redis_connection
from redis.exceptions import LockNotOwnedError

from config.logging_config import logger

LOCK_KEY = "migrations:lock"
LOCK_TTL = 300  # 5 minutes - max time for migrations to complete
LOCK_WAIT_TIMEOUT = 600  # 10 minutes - max time to wait for lock to be released
LOCK_CHECK_INTERVAL = 1  # how often to poll for the lock while waiting

# cache.lock() (below) stores its token via a raw SET, not through
# django-redis's own pickle serializer - reading that value back with
# cache.get() throws UnpicklingError (confirmed directly: 'invalid load
# key' on the raw bytes), so peeking at who currently holds the lock (for
# logging only - never for the acquire/release logic itself, which the
# Lock object already handles atomically) goes through this raw connection
# and cache.make_key() instead, matching how the value was actually written.
_redis = get_redis_connection("default")


class Command(BaseCommand):
    help = (
        "Run Django migrations with a distributed lock to prevent multiple "
        "pods from migrating simultaneously."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "app_label",
            nargs="?",
            help="App label or list of app labels of the application to migrate.",
        )
        parser.add_argument(
            "migration_name",
            nargs="?",
            help='Django migration name, e.g. "0001_initial" or "zero" to unapply all migrations.',
        )

    def handle(self, *args, **options) -> None:
        app_label = options.get("app_label")
        migration_name = options.get("migration_name")
        verbosity = options.get("verbosity", 1)

        # hostname prefix is just for readable logs/redis-cli GET output
        # (which pod holds/held the lock); the uuid suffix is the actual
        # per-acquisition token cache.lock() (django-redis, backed by
        # redis-py's own Lock) uses to tell "the lock I hold right now"
        # apart from "a lock this same host held earlier that already
        # expired and was re-acquired by someone else" - matters once the
        # lock can expire while this process is still running.
        hostname = socket.gethostname()
        lock_id = f"{hostname}:{uuid.uuid4().hex[:8]}"

        self._lock = cache.lock(
            LOCK_KEY,
            timeout=LOCK_TTL,
            blocking_timeout=LOCK_WAIT_TIMEOUT,
            sleep=LOCK_CHECK_INTERVAL,
        )

        if held_by := _redis.get(cache.make_key(LOCK_KEY)):
            logger.info(
                "migration lock held, waiting for release",
                owner=held_by.decode(),
                wait_timeout_seconds=LOCK_WAIT_TIMEOUT,
            )

        if not self._lock.acquire(token=lock_id):
            logger.error("timed out waiting for migration lock", wait_timeout_seconds=LOCK_WAIT_TIMEOUT)
            raise CommandError("Migration lock unavailable")

        logger.info("acquired migration lock", owner=lock_id, ttl_seconds=LOCK_TTL)
        try:
            logger.debug("running migrations", app_label=app_label, migration_name=migration_name)
            migrate_args = []
            if app_label:
                migrate_args.append(app_label)
            if migration_name:
                migrate_args.append(migration_name)

            call_command("migrate", *migrate_args, interactive=False, verbosity=verbosity)
            logger.debug("migrations complete", app_label=app_label, migration_name=migration_name)
        finally:
            self._release_lock()

    def _release_lock(self) -> None:
        """Release the distributed lock - but only if it's still the one this process acquired.

        cache.lock() (django-redis, backed by redis-py's own Lock class)
        does this compare-and-delete atomically via a Lua script executed
        server-side in one round trip - GET, compare the stored token,
        DEL only on a match - rather than a hand-rolled WATCH/MULTI
        transaction. LockNotOwnedError means the lock already expired and
        was re-acquired by someone else while this process was still
        running, which is expected and left alone rather than raised.
        """
        try:
            self._lock.release()
            logger.debug("released migration lock")
        except LockNotOwnedError:
            logger.debug("lock changed before release - left it alone")
        except Exception as exc:
            logger.warning("could not release migration lock", exc_info=exc)
