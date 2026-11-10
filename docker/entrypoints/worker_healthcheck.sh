#!/bin/sh
# Worker health probe - reads the heartbeat files config/health.py writes,
# no Python and no Celery import. Usage: worker_healthcheck.sh <mode>
#
#   ready  worker-ready exists (boot finished; use as the startup probe)
#   live   worker-alive is younger than the max age (use as the liveness probe)
#
# WORKER_HEALTH_DIR (default /tmp) and WORKER_HEALTHCHECK_MAX_AGE (default
# 600, in seconds) override the location and threshold.

dir="${WORKER_HEALTH_DIR:-/tmp}"
max_age="${WORKER_HEALTHCHECK_MAX_AGE:-600}"

case "$1" in
  ready) [ -f "$dir/worker-ready" ] ;;
  live)
    [ -f "$dir/worker-alive" ] || exit 1
    read -r stamp <"$dir/worker-alive" || exit 1
    case "$stamp" in '' | *[!0-9]*) exit 1 ;; esac
    [ $(($(date +%s) - stamp)) -lt "$max_age" ]
    ;;
  *)
    echo "usage: $0 ready|live" >&2
    exit 2
    ;;
esac
