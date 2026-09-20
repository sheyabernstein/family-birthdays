from notifications.enums import ChannelEnum, ShiftReason


def test_channel_enum_value_is_stable_and_lowercase_distinct_from_label():
    assert ChannelEnum.EMAIL.value == "email"
    assert ChannelEnum.EMAIL.label == "Email"
    assert ChannelEnum.SMS.value == "sms"
    assert ChannelEnum.SMS.label == "SMS"


def test_channel_enum_str_is_its_own_value():
    # accounts.magic_links.issue_token interpolates a ChannelEnum member
    # directly into an f-string payload, relying on this - see AGENTS.md's
    # "Enums" section.
    assert str(ChannelEnum.EMAIL) == "email"
    assert f"{ChannelEnum.SMS}" == "sms"


def test_shift_reason_value_is_stable_and_lowercase_distinct_from_label():
    assert ShiftReason.SHABBOS.value == "shabbos"
    assert ShiftReason.SHABBOS.label == "Shabbos"
    assert ShiftReason.YOM_TOV.value == "yom_tov"
    assert ShiftReason.YOM_TOV.label == "Yom Tov"
