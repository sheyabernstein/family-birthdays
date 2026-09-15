from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.views.generic import View


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
        except Exception:  # noqa E401
            checks["database"] = "error"
            ok = False

        try:
            cache.set("readyz", "1", 5)
            checks["cache"] = "ok" if cache.get("readyz") == "1" else "error"
            ok = ok and checks["cache"] == "ok"
        except Exception:  # noqa E401
            checks["cache"] = "error"
            ok = False

        return JsonResponse(
            {"status": "ok" if ok else "degraded", "checks": checks},
            status=200 if ok else 503,
        )
