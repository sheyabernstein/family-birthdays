#!/bin/sh

set -e

python "/app/docker/scripts/wait_for_postgres.py"

# migrate_with_lock runs DB migrations before gunicorn starts - no --settings
# flag needed, manage.py already defaults DJANGO_SETTINGS_MODULE itself
python manage.py migrate_with_lock

# _run_with_metrics.sh runs this alongside a dedicated Prometheus metrics
# server on :9090 - see that script's own comment for why (no tini needed).
exec /app/docker/entrypoints/_run_with_metrics.sh \
  gunicorn config.wsgi:application \
  --config /app/config/gunicorn_conf.py \
  --bind "0.0.0.0:8000" \
  --name "$BUILD_NAME" \
  --workers="${WEB_CONCURRENCY:-2}" \
  --timeout "${WEB_TIMEOUT:-30}"
