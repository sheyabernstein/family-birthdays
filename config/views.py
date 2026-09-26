import socket

from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.views.generic import View

from config.logging_config import logger


def _reason(exc: Exception) -> str:
    """Collapses a driver's own multi-line error text (e.g. psycopg2's caret-pointer line) to one line."""
    return " ".join(str(exc).split())


class HealthView(View):
    """Liveness probe: proves the WSGI worker responds.

    No dependency checks - if this fails k8s restarts the pod, so a
    DB/Redis blip must not trip it.
    """

    http_method_names = ["get"]

    @staticmethod
    def get(*args, **kwargs) -> HttpResponse:
        return JsonResponse({"status": "ok"}, status=200)


class ReadyView(View):
    """Readiness probe: can this pod serve a request right now?

    Checks critical dependencies; a failure pulls the pod out of the
    Service without a restart.
    """

    http_method_names = ["get"]

    @staticmethod
    def get(*args, **kwargs) -> HttpResponse:
        checks = {}
        ok = True

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = "error"
            ok = False
            logger.warning("readyz check failed", check="database", reason=_reason(exc))

        readyz_key = f"readyz:{socket.gethostname()}"

        try:
            cache.set(readyz_key, "1", 5)
            if cache.get(readyz_key) != "1":
                raise RuntimeError("round-trip mismatch")
            checks["cache"] = "ok"
        except Exception as exc:
            checks["cache"] = "error"
            ok = False
            logger.warning("readyz check failed", check="cache", reason=_reason(exc))

        return JsonResponse(
            {"status": "ok" if ok else "degraded", "checks": checks},
            status=200 if ok else 503,
        )
