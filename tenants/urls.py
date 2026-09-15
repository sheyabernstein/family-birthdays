from django.urls import path

from tenants import views

app_name = "tenants"

urlpatterns = [
    path("create/", views.CreateFamilyView.as_view(), name="create_family"),
    path("switch/", views.SwitchFamilyView.as_view(), name="switch_family"),
    path("settings/", views.FamilySettingsView.as_view(), name="family_settings"),
    path("no-family/", views.NoFamilyAccessView.as_view(), name="no_family_access"),
]
