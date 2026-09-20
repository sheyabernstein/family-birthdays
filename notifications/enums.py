from enum import StrEnum


class ChannelEnum(StrEnum):
    """The single definition of a notification channel - email or SMS.

    A plain enum with no Django import, so it's safely importable from
    anywhere (including accounts, which notifications.models itself
    depends on) with no risk of a circular import - the reason this
    isn't a models.TextChoices living on a model instead.
    """

    EMAIL = "email"
    SMS = "sms"

    @property
    def label(self) -> str:
        return {ChannelEnum.EMAIL: "Email", ChannelEnum.SMS: "SMS"}[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """(value, label) pairs, in the shape a CharField's choices= expects."""
        return [(member.value, member.label) for member in cls]


class ShiftReason(StrEnum):
    """Why an Occurrence's send_date landed before its occurrence_date - see family.hebrew.resolve_send_date.

    A plain enum, not models.TextChoices, for the same reason as
    ChannelEnum above - family.hebrew (which computes this) can't import
    from notifications.models without inverting this app's usual
    dependency direction, but a Django-free enum carries no such risk
    either way.

    Unlike ChannelEnum, the value *is* the display text (no separate
    label) - this is only ever stored (Occurrence.shift_reasons, a
    JSONField) and displayed, never compared against as a machine code
    the way ChannelEnum's values are throughout the rest of this app, so
    there's nothing a second, human-readable label would add.
    """

    SHABBOS = "Shabbos"
    YOM_TOV = "Yom Tov"
