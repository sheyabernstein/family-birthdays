from django.contrib import admin
from django.urls import include, path

from config.views import HealthView, ReadyView

urlpatterns = [
    path("healthz", HealthView.as_view(), name="healthz"),
    path("readyz", ReadyView.as_view(), name="readyz"),
    # Ahead of admin.site.urls so this staff-only page under /admin/ is
    # matched first - it's not part of the ModelAdmin registry, just a
    # plain view sharing the admin's look via base_site.html.
    path("", include("notifications.urls", namespace="notifications")),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls", namespace="accounts")),
    path("family/", include("tenants.urls", namespace="tenants")),
    path("", include("family.urls", namespace="family")),
]
