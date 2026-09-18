#!/bin/sh

# Shared by web.sh/worker.sh: runs a single-worker gunicorn process
# serving Prometheus metrics on :9090 (see
# config/observability/metrics_wsgi.py) in a respawn loop as a background
# sibling, then execs into the real command so it becomes PID 1.
#
# The respawn loop exists because a metrics-server crash otherwise means
# permanently losing :9090 for the rest of the container's life - nothing
# else in this process tree would ever restart it (verified directly: the
# main app has no reason to `wait()` on a child it never forked, so a
# killed metrics master just sits there as an unreaped zombie until the
# container's next unrelated restart). Deliberately not a Docker
# `HEALTHCHECK` on :9090 either - that's Docker-specific and kubelet
# ignores it outright in k8s, so it'd mean maintaining a second, redundant
# probing mechanism that only covers one of the two deployment targets;
# a plain retry loop here works identically under both.
#
# An earlier version backgrounded both processes with a single, one-shot
# start (no loop) and used a trap + `wait` (no PID given) to tear both
# down together - rejected because a bare `wait` only returns once
# *every* backgrounded job has exited, so the main app crashing while the
# metrics server stayed running never ended the container, silently
# defeating `restart: unless-stopped` (docker-compose.yml). Handing PID 1
# to the real command instead makes its crash/exit behavior - and how
# `docker stop`'s SIGTERM reaches it - identical to how it worked before
# this metrics sidecar existed; the respawn loop below runs independently
# of that and has no bearing on it.

set -e

# --config shares the same structlog JSON logconfig_dict as the main app
# (config/gunicorn_conf.py) - without it this process's own startup/access
# lines fall back to gunicorn's plain-text default, the only log lines in
# the container not in the shared JSON shape. GUNICORN_CMD_ARGS is set
# here (shell prefix assignment, scoped to just this one command) with
# its own distinct --control-socket path, deliberately not a
# container-wide Dockerfile ENV - web.sh sets its own for the main app's
# gunicorn the same way, at that call site, since both processes sharing
# one path would mean two masters racing to bind the same socket file.
# An empty/unset GUNICORN_CMD_ARGS doesn't disable the control socket
# either - gunicorn still falls back to its own default, a path under the
# CWD (/app/.gunicorn/...), which fails outright in dev
# (docker-compose.dev.yml bind-mounts /app from the host, and that mount
# doesn't support UNIX domain sockets) - so this needs an explicit path
# of its own, not just "no args".
(
  while true; do
    # If the previous master died but its own worker didn't (e.g. only
    # the master got killed/OOM'd, not the whole process group), the
    # worker keeps :9090 bound until it notices on its own - a sync
    # worker only checks its master is still alive between requests, so
    # this can briefly log a handful of "Address already in use" retries
    # (confirmed directly: recovered on its own within ~15s) before the
    # stale worker exits and a new master can bind. Not worth chasing
    # further - this is a best-effort side metrics endpoint, not
    # user-facing, and it always does recover without help.
    GUNICORN_CMD_ARGS="--control-socket /tmp/gunicorn-metrics.ctl" \
      gunicorn config.observability.metrics_wsgi:application \
      --config /app/config/gunicorn_conf.py \
      --bind 0.0.0.0:9090 --workers=1 || true
    sleep 1
  done
) &

exec "$@"
