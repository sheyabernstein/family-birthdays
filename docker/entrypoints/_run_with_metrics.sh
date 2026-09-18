#!/bin/sh

# Shared by web.sh/worker.sh: runs the given main app command alongside a
# single-worker gunicorn process serving Prometheus metrics on :9090 (see
# config/observability/metrics_wsgi.py), forwarding SIGTERM/SIGINT to both
# so `docker stop`/k8s termination shuts both down cleanly. Plain sh trap +
# background PIDs + wait - no tini - since neither the main process nor the
# metrics server here spawns/abandons grandchildren that would need a real
# PID-1 zombie reaper; this script itself already is PID 1 (see Dockerfile's
# CMD, which has no separate ENTRYPOINT).

set -e

trap 'kill -TERM "$MAIN_PID" "$METRICS_PID" 2>/dev/null; wait' TERM INT

"$@" &
MAIN_PID=$!

# --config shares the same structlog JSON logconfig_dict as the main app
# (config/gunicorn_conf.py) - without it this process's own startup/access
# lines fall back to gunicorn's plain-text default, the only log lines in
# the container not in the shared JSON shape. GUNICORN_CMD_ARGS is cleared
# for just this command (shell prefix assignment, scoped to this one
# invocation) - it's set container-wide to pin the *main* app's control
# socket path (--control-socket /tmp/gunicorn.ctl, see the Dockerfile),
# and both gunicorn processes inheriting it would mean two masters racing
# to bind the same socket file; this one has no need for a control socket
# of its own.
GUNICORN_CMD_ARGS="" gunicorn config.observability.metrics_wsgi:application \
  --config /app/config/gunicorn_conf.py \
  --bind 0.0.0.0:9090 --workers=1 &
METRICS_PID=$!

wait
