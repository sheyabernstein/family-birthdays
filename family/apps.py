from django.apps import AppConfig


class FamilyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "family"

    def ready(self) -> None:
        from family import signals  # noqa: F401
