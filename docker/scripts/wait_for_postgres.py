import os
import sys
from time import sleep, time

import psycopg2

from config.helpers import get_env_int
from config.logging_config import logger

HOST = os.getenv("POSTGRES_HOST")
PORT = get_env_int("POSTGRES_PORT", 5432)
USER = os.getenv("POSTGRES_USER")
PASS = os.getenv("POSTGRES_PASSWORD")
DATABASE = os.getenv("POSTGRES_DB")
SSLMODE = os.getenv("POSTGRES_SSLMODE", "prefer")

MAX_TRIES = get_env_int("WAIT_FOR_POSTGRES_MAX_TRIES", default=10)
SLEEP_BETWEEN = get_env_int("WAIT_FOR_POSTGRES_SLEEP_BETWEEN", default=2)

COMMON_KWARGS = {
    "host": HOST,
    "port": PORT,
    "user": USER,
    "database": DATABASE,
}


def _check_connection() -> Exception | None:
    """Return None if PostgreSQL is accessible, otherwise the exception raised trying."""
    try:
        psycopg2.connect(
            host=HOST,
            port=PORT,
            user=USER,
            password=PASS,
            database=DATABASE,
            connect_timeout=SLEEP_BETWEEN,
            sslmode=SSLMODE,
        )
    except Exception as exc:  # noqa: BLE001 — intentionally broad catch
        return exc

    return None


def wait_for_postgres() -> None:
    logger.debug("attempting to connect to postgresql", **COMMON_KWARGS)

    for attempt in range(1, MAX_TRIES + 1):
        start = time()
        error = _check_connection()

        if error is None:
            logger.info("connected to postgresql", **COMMON_KWARGS, attempt=attempt, max_tries=MAX_TRIES)
            sys.exit(0)

        elapsed = time() - start
        if elapsed < SLEEP_BETWEEN:
            sleep(SLEEP_BETWEEN - elapsed)

        if attempt >= MAX_TRIES:
            logger.error(
                "could not connect to postgresql",
                **COMMON_KWARGS,
                attempt=attempt,
                max_tries=MAX_TRIES,
                exc_info=error,
            )
            sys.exit(1)

        logger.warning(
            "error connecting to postgresql",
            **COMMON_KWARGS,
            attempt=attempt,
            max_tries=MAX_TRIES,
            exc_info=error,
        )


if __name__ == "__main__":
    wait_for_postgres()
