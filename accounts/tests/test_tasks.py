import pytest

from accounts.tasks import send_magic_link_message
from notifications.enums import ChannelEnum
from notifications.sms import SmsRateLimitedError, SmsUnrecoverableError


def test_send_magic_link_message_sends_email(monkeypatch):
    calls = []
    monkeypatch.setattr("accounts.tasks.send_email", lambda **kwargs: calls.append(kwargs))

    send_magic_link_message(
        account_uuid="00000000-0000-0000-0000-000000000000",
        channel=ChannelEnum.EMAIL,
        destination="someone@example.com",
        subject="Your sign-in link",
        body="plain body",
        html="<p>html body</p>",
        from_name="Rokach Family",
        from_email="noreply-rokach@example.com",
        reply_to="",
        sms_sender_id="",
    )

    assert len(calls) == 1
    assert calls[0]["to"] == "someone@example.com"
    assert calls[0]["event_type"] == "magic_link"
    assert calls[0]["html"] == "<p>html body</p>"


def test_send_magic_link_message_sends_sms(monkeypatch):
    calls = []
    monkeypatch.setattr("accounts.tasks.send_sms", lambda **kwargs: calls.append(kwargs))

    send_magic_link_message(
        account_uuid="00000000-0000-0000-0000-000000000000",
        channel=ChannelEnum.SMS,
        destination="+15551234567",
        subject="",
        body="Your sign-in link: https://example.com/x",
        html="",
        from_name="",
        from_email="",
        reply_to="",
        sms_sender_id="RokachFam",
    )

    assert len(calls) == 1
    assert calls[0]["to"] == "+15551234567"
    assert calls[0]["sender_id"] == "RokachFam"
    assert calls[0]["event_type"] == "magic_link"


def test_send_magic_link_message_does_not_retry_an_unrecoverable_error(monkeypatch):
    def _raise_unrecoverable(**kwargs):
        raise SmsUnrecoverableError("bad number")

    monkeypatch.setattr("accounts.tasks.send_sms", _raise_unrecoverable)

    with pytest.raises(SmsUnrecoverableError):
        send_magic_link_message(
            account_uuid="00000000-0000-0000-0000-000000000000",
            channel=ChannelEnum.SMS,
            destination="+15551234567",
            subject="",
            body="Your sign-in link",
            html="",
            from_name="",
            from_email="",
            reply_to="",
            sms_sender_id="",
        )


def test_send_magic_link_message_retries_a_rate_limit_error(monkeypatch):
    def _raise_rate_limited(**kwargs):
        raise SmsRateLimitedError("10 publishes attempted, limit is 8/s")

    monkeypatch.setattr("accounts.tasks.send_sms", _raise_rate_limited)

    with pytest.raises(SmsRateLimitedError):
        send_magic_link_message(
            account_uuid="00000000-0000-0000-0000-000000000000",
            channel=ChannelEnum.SMS,
            destination="+15551234567",
            subject="",
            body="Your sign-in link",
            html="",
            from_name="",
            from_email="",
            reply_to="",
            sms_sender_id="",
        )
