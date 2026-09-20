from enum import StrEnum


class ChannelEnum(StrEnum):
    """The single definition of a notification channel - email or SMS.

    A plain enum with no Django import, so it's safely importable from
    anywhere (including accounts, which notifications.models itself
    depends on) with no risk of a circular import - the reason this
    isn't a models.TextChoices living on a model instead.

    The value *is* the display text ("Email"/"SMS", not "email"/"sms") -
    this app's convention is that an enum's value is always the friendly
    text, not a separate machine code, unless something genuinely needs
    a stable identifier decoupled from what's shown to a user (see
    accounts.magic_links, which deliberately serializes .name.lower()
    rather than a ChannelEnum member into its own long-lived Redis
    payload, for exactly that reason - anything serialized outside the
    request/response cycle shouldn't be coupled to display text that
    could change). Everywhere else - NotificationPreference.channel/
    Message.channel's own choices=, every POST'd "channel" form field,
    every equality check against ChannelEnum.EMAIL/SMS - reads/writes
    this same friendly value directly; there's deliberately no longer a
    separate label to keep in sync with it.
    """

    EMAIL = "Email"
    SMS = "SMS"

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """(value, value) pairs, in the shape a CharField's choices= expects - the value already is the label."""
        return [(member.value, member.value) for member in cls]


class ShiftReason(StrEnum):
    """Why an Occurrence's send_date landed before its occurrence_date - see family.hebrew.resolve_send_date.

    A plain enum, not models.TextChoices, for the same reason as
    ChannelEnum above - family.hebrew (which computes this) can't import
    from notifications.models without inverting this app's usual
    dependency direction, but a Django-free enum carries no such risk
    either way. Same value-is-the-label convention as ChannelEnum too.
    """

    SHABBOS = "Shabbos"
    YOM_TOV = "Yom Tov"
