FROM python:3.14.7-alpine AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    GUNICORN_CMD_ARGS="--control-socket /tmp/gunicorn.ctl" \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prom_multiproc

RUN apk add --no-cache curl \
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

COPY --from=build --chown=app:app /opt/venv /opt/venv
COPY --from=static --chown=app:app /app/staticfiles /app/staticfiles

COPY --chown=app:app . .

ENV BUILD_NAME="${BUILD_NAME}" \
    BUILD_VERSION="${BUILD_VERSION}"

USER app

EXPOSE 8000 9090
CMD ["/app/docker/entrypoints/web.sh"]
