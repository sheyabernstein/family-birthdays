#!/bin/sh

set -e

python "/app/docker/scripts/wait_for_postgres.py"

# Same migrate_with_lock call as web.sh, for the same reason - the worker
# has no startup dependency on web (see docker-compose.yml) and runs its
# own embedded Beat schedule straight against the DB, so nothing otherwise
# guarantees migrations have finished before it starts querying a possibly
# stale schema. Safe to call from both entrypoints concurrently by design -
# see migrate_with_lock's own docstring and AGENTS.md's note on it.
python manage.py migrate_with_lock

# --beat embeds beat in the worker process (RedBeat's Redis lock makes
# this safe even if the service is scaled to multiple replicas) - see
# CELERY_BEAT_SCHEDULER comment in config/settings.py, which is what
# actually selects RedBeat; no CLI flag needed. Don't add -S here -
# on `celery worker` (unlike `celery beat`), -S is short for
# --statedb, the worker's own local state file, not --scheduler.

# _run_with_metrics.sh runs this alongside a dedicated Prometheus metrics
# server on :9090 - see that script's own comment for why (no tini needed).
exec /app/docker/entrypoints/_run_with_metrics.sh \
  celery -q -A config worker --beat \
  -l info \
  --concurrency "${CELERY_CONCURRENCY:-1}" \
  -n "$(uname -n):${BUILD_VERSION}"
