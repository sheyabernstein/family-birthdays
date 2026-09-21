from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"

    def ready(self) -> None:
        # Validates whichever SmsBackend settings.SMS_BACKEND names - here,
        # not in config/settings.py, since resolving the class requires
        # importing notifications.sms (which reads django.conf.settings),
        # and by ready() time settings and the app registry are both fully
        # loaded, unlike mid-execution of settings.py itself.
        from django.conf import settings
        from django.utils.module_loading import import_string

        from notifications import signals  # noqa: F401

        import_string(settings.SMS_BACKEND).validate_settings()
