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
# server on :9090 - see that script's own comment, and the Dockerfile's
# tini ENTRYPOINT, for the full reasoning.

# -Q high,normal,low - order matters, not just membership. Combined with
# CELERY_BROKER_TRANSPORT_OPTIONS' queue_order_strategy="priority"
# (config/settings.py), this is what actually gives "high" its priority:
# the worker drains high before ever touching normal/low, no dedicated
# per-tier consumer process needed. See config.enums.TaskPriority's own
# docstring for why this replaced Celery/kombu's native per-message Redis
# priority (a live kombu bug, found the hard way against this exact stack).
exec /app/docker/entrypoints/_run_with_metrics.sh \
  celery -q -A config worker --beat \
  -l info \
  -Q high,normal,low \
  --concurrency "${CELERY_CONCURRENCY:-1}" \
  -n "$(uname -n):${BUILD_VERSION}"
