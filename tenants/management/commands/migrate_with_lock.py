import socket
import time
import uuid
from typing import Any

import redis
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from config.logging_config import logger

# Talks to Redis directly rather than through Django's cache framework -
# the default CACHES backend is per-process LocMemCache, which would make
# this lock local to whichever pod happens to run it, defeating the whole
# point. Mirrors accounts/magic_links.py, the first module to need genuinely
# shared cross-process state for the same reason.
_redis_client = redis.from_url(settings.REDIS_URL)

LOCK_KEY = "migrations:lock"
LOCK_TTL = 300  # 5 minutes - max time for migrations to complete
LOCK_WAIT_TIMEOUT = 600  # 10 minutes - max time to wait for lock to be released
LOCK_CHECK_INTERVAL = 1  # Check every 1 second


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

        # hostname prefix is just for readable logs (which pod holds/held
        # the lock); the uuid suffix is what actually makes this unique
        # per acquisition, which lets _release_lock tell "the lock I
        # acquired" apart from "a lock this same host acquired some other
        # time" - matters once the lock can expire and be re-acquired by
        # someone else while this process is still running.
        hostname = socket.gethostname()
        uid = uuid.uuid4().hex[:8]
        self._lock_id = f"{hostname}:{uid}"

        if not self._acquire_lock():
            logger.error("migration lock unavailable", wait_timeout_seconds=LOCK_WAIT_TIMEOUT)
            raise CommandError("Migration lock unavailable")

        try:
            # Run migrations
            logger.debug("running migrations", app_label=app_label, migration_name=migration_name)
            migrate_args = []
            if app_label:
                migrate_args.append(app_label)
            if migration_name:
                migrate_args.append(migration_name)

            call_command("migrate", *migrate_args, interactive=False, verbosity=verbosity)
            logger.debug("migrations complete", app_label=app_label, migration_name=migration_name)
        finally:
            # Always release lock
            self._release_lock()

    def _acquire_lock(self) -> bool:
        """Try to acquire a distributed lock.

        Waits up to LOCK_WAIT_TIMEOUT seconds for the lock to become
        available.

        Returns:
            Whether the lock was actually acquired before timing out.
        """
        start_time = time.time()
        lock_owner_logged = False

        while True:
            # Set the lock key only if it doesn't exist, with a TTL, in one
            # atomic call.
            acquired = _redis_client.set(LOCK_KEY, self._lock_id, nx=True, ex=LOCK_TTL)

            if acquired:
                logger.info("acquired migration lock", owner=self._lock_id, ttl_seconds=LOCK_TTL)
                return True

            elapsed = time.time() - start_time
            if elapsed > LOCK_WAIT_TIMEOUT:
                logger.error("timed out waiting for migration lock", elapsed_seconds=elapsed)
                return False

            if owner := _redis_client.get(LOCK_KEY):
                owner = owner.decode()

            remaining = LOCK_WAIT_TIMEOUT - elapsed

            if not lock_owner_logged:
                logger.info(
                    "migration lock held, waiting for release", owner=owner, remaining_seconds=remaining
                )
                lock_owner_logged = True
            else:
                logger.debug("still waiting for migration lock", owner=owner, remaining_seconds=remaining)
            time.sleep(LOCK_CHECK_INTERVAL)

    def _release_lock(self) -> None:
        """Release the distributed lock - but only if it's still the one this process acquired.

        A plain GET-then-DEL would have a race where a lock that expired
        and was re-acquired by another pod gets deleted out from under
        that pod instead of left alone, so this uses WATCH/MULTI as a
        compare-and-delete: the transaction only commits if the key
        hasn't changed since the GET, and a change - e.g. someone else
        acquiring it - aborts it harmlessly (redis.WatchError).
        """
        try:
            with _redis_client.pipeline() as pipe:
                pipe.watch(LOCK_KEY)
                if pipe.get(LOCK_KEY) == self._lock_id.encode():
                    pipe.multi()
                    pipe.delete(LOCK_KEY)
                    pipe.execute()
            logger.debug("released migration lock")
        except redis.WatchError:
            logger.debug("lock changed before release - left it alone")
        except Exception as exc:
            logger.warning("could not release migration lock", exc_info=exc)
