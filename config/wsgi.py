"""WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.conf import settings
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Before get_wsgi_application(), not after: that call's own WSGIHandler()
# construction is what compiles settings.MIDDLEWARE into the actual
# request-handling chain (WSGIHandler.__init__ -> load_middleware()) -
# DjangoInstrumentor().instrument() (inside init_tracing()) works by
# inserting its own middleware into that same list, so calling it any
# later means mutating a list nobody reads again; the request span it's
# meant to create for every view never actually gets built. Reading
# django.conf.settings here (which DjangoInstrumentor needs) still fully
# executes config/settings.py on first access regardless of get_wsgi_
# application() not having run yet - see config/observability/tracing.py's
# own docstring for why this still can't safely happen from inside
# config/settings.py itself.
from config.observability.tracing import init_tracing  # noqa: E402

init_tracing()

application = get_wsgi_application()

# Set once per gunicorn worker process, after the app's fully built - see
# config/observability/metrics.py's own docstring. Grafana's namespace
# variable (docker/observability/grafana-dashboards/notifications.json)
# queries label_values() against this metric, so a process that never
# calls this leaves the dashboard with no namespace to select at all -
# found for real, the first time this dashboard was actually opened.
from config.observability.metrics import set_build_info  # noqa: E402

set_build_info(settings.BUILD_VERSION)
