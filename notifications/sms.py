"""Pluggable SMS backends.

Selected via settings.SMS_BACKEND, a dotted class path (same shape as
STORAGES["staticfiles"]["BACKEND"]), not a magic string.
notifications.services.send_sms resolves and caches one instance of
whichever backend is configured; add a new provider by subclassing
SmsBackend here and pointing SMS_BACKEND at it - nothing else needs to
change.
"""

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.exceptions import FamilyBirthdaysError
from config.logging_config import logger


class SmsUnrecoverableError(FamilyBirthdaysError):
    """A send failure that retrying can't fix (bad number, opted out, bad auth, ...).

    notifications.tasks.send_message catches this separately from a
    transient failure and does not retry it.
    """


class SmsBackend:
    def send(self, *, to: str, body: str, sender_id: str) -> dict:
        raise NotImplementedError

    @classmethod
    def validate_settings(cls) -> None:
        """Raises ImproperlyConfigured if this backend's required settings are missing.

        No-op by default; called once from NotificationsConfig.ready() for
        whichever backend settings.SMS_BACKEND names - not from
        config/settings.py itself, since resolving the class means
        importing this module, which reads django.conf.settings, while
        config/settings.py would still be mid-execution as the exact
        module that settings object resolves to.
        """


class ConsoleSmsBackend(SmsBackend):
    """Logs instead of sending - the default, for local dev and tests."""

    def send(self, *, to: str, body: str, sender_id: str) -> dict:
        logger.info("sms send", backend="console", to=to, body=body, sender_id=sender_id)
        return {"status": "logged", "to": to, "sender_id": sender_id}


class SnsSmsBackend(SmsBackend):
    """Sends via AWS SNS's Publish API.

    Alphanumeric Sender ID (AWS.SNS.SMS.SenderID, from a Family's own
    sms_sender_id) isn't supported in the US/Canada - AWS silently drops it
    and sends from a shared/random long code instead. Nothing to fix here;
    just don't be surprised when a US test message shows no sender
    branding.

    SMSType is "Transactional", not "Promotional" - AWS prioritizes
    delivery (over cost) for these, right for a birthday/yahrzeit
    notification.
    """

    # botocore SNS error codes that are permanent, not worth retrying.
    # Deliberately a denylist, not an allowlist: an error code this app
    # doesn't yet recognize should fail safe toward "retry it", not toward
    # "give up silently".
    _UNRECOVERABLE_CODES = frozenset(
        {
            "InvalidParameterException",
            "ParameterValueInvalidException",
            "AuthorizationErrorException",
            "OptInRequiredException",
        }
    )

    @classmethod
    def validate_settings(cls) -> None:
        missing = [
            name
            for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SNS_REGION")
            if not getattr(settings, name, "")
        ]
        if missing:
            raise ImproperlyConfigured(
                f"SMS_BACKEND=notifications.sms.SnsSmsBackend requires {', '.join(missing)} to be set."
            )

    def __init__(self) -> None:
        # Built once and reused for every send (botocore clients are safe
        # to share across calls) - not at import/settings-load time, since
        # Celery's prefork workers fork before running any task, and a
        # client constructed in the parent beforehand is exactly the kind
        # of thing that can behave oddly post-fork.
        self._client = boto3.client(
            "sns",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_SNS_REGION,
        )

    def send(self, *, to: str, body: str, sender_id: str) -> dict:
        try:
            response = self._client.publish(
                PhoneNumber=to,
                Message=body,
                MessageAttributes={
                    "AWS.SNS.SMS.SenderID": {"DataType": "String", "StringValue": sender_id},
                    "AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"},
                },
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            logger.warning("sns publish failed", to=to, sender_id=sender_id, error_code=code, exc_info=exc)
            if code in self._UNRECOVERABLE_CODES:
                raise SmsUnrecoverableError(str(exc)) from exc
            raise

        message_id = response["MessageId"]
        logger.info("sms sent", backend="sns", to=to, sender_id=sender_id, message_id=message_id)
        return {"message_id": message_id}
