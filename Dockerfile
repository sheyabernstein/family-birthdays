FROM python:3.14.7-alpine AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prom_multiproc

RUN apk add --no-cache curl tini \
    && addgroup -S app \
    && adduser -S -G app -h /app app \
    && mkdir -p "${PROMETHEUS_MULTIPROC_DIR}" \
    && chown -R app:app "${PROMETHEUS_MULTIPROC_DIR}"


FROM base AS build

ENV VIRTUAL_ENV=/opt/venv \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN apk add --no-cache build-base libffi-dev openssl-dev

RUN pip install --no-cache-dir poetry

RUN python -m venv /opt/venv

WORKDIR /app
COPY pyproject.toml poetry.lock ./

RUN poetry install --no-root --only main


FROM build AS static

COPY . .

RUN python manage.py collectstatic --noinput


FROM base AS app

ARG BUILD_NAME=family-birthdays
ARG BUILD_VERSION=dev
ARG BUILD_SHA=dev

COPY --from=build --chown=app:app /opt/venv /opt/venv
COPY --from=static --chown=app:app /app/staticfiles /app/staticfiles

COPY --chown=app:app . .

ENV BUILD_NAME="${BUILD_NAME}" \
    BUILD_VERSION="${BUILD_VERSION}" \
    BUILD_SHA="${BUILD_SHA}"

USER app

EXPOSE 8000 9090

# tini is real PID 1: it forwards signals to its one child correctly and,
# more importantly here, reaps the metrics sidecar (_run_with_metrics.sh
# execs into the real app, so that sidecar ends up parented to whatever
# occupies PID 1's process slot afterward - the app's own arbiter/worker
# loop has no reason to know about a process it never forked, so nothing
# else guarantees it gets waited on when it exits). Without tini, this
# app's CMD script would have to reimplement that generic reaping itself
# to avoid an accumulating zombie every time the sidecar restarts.
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/app/docker/entrypoints/web.sh"]
