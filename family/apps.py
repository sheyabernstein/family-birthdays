from django.apps import AppConfig
from django.utils import dateformat


class FamilyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "family"

    def ready(self) -> None:
        from family import signals  # noqa: F401

        # Patches Django's own weekday-name lookup (index 5 = Saturday) so
        # every |date:"l"/"D" call site says "Shabbos" with no per-template
        # edits - process-wide, all tenants, no abbreviation. See AGENTS.md's
        # "Dates" section for why this only works through Django's own date
        # formatting, never strftime.
        dateformat.WEEKDAYS[5] = "Shabbos"
        dateformat.WEEKDAYS_ABBR[5] = "Shabbos"
