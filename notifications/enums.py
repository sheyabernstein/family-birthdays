from django.db import models


class ChannelEnum(models.TextChoices):
    """The single definition of a notification channel - email or SMS.

    A standalone models.TextChoices, not nested inside a Model the way
    Role/Union.Status/EventType.Anchor/etc. are - accounts needs to
    import this, and nesting it inside notifications.models would
    invert this app's usual dependency direction. django.db.models
    itself carries no such risk either way - it's a framework import,
    not a dependency on this app's own models - so this is still a real
    models.TextChoices, same NAME = "value", "Label" shape and free
    .label/.choices as every other enum in this app. See AGENTS.md's
    "Enums" section.
    """

    EMAIL = "email", "Email"
    SMS = "sms", "SMS"


class ShiftReason(models.TextChoices):
    """Why an Occurrence's send_date landed before its occurrence_date - see family.hebrew.resolve_send_date.

    Standalone for the same reason as ChannelEnum above. The stored
    value (Occurrence.shift_reasons, a JSONField holding a list of
    these) is deliberately not the same string as .label - see
    AGENTS.md's "Enums" section.
    """

    SHABBOS = "shabbos", "Shabbos"
    YOM_TOV = "yom_tov", "Yom Tov"


class NotificationEventTypeLabel(models.TextChoices):
    """Synthetic event_type labels for send_email/send_sms, not backed by any seeded EventType row.

    Distinct from EventType.BuiltinCode (a real seeded row per value) -
    see notifications.tasks._metric_event_type for how the two combine
    into one label space for the notifications_*_sent_total metrics and
    the "sms/email sent" log lines.
    """

    CUSTOM = "custom", "Custom event type"
    MAGIC_LINK = "magic_link", "Magic link sign-in"
