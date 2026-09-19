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
