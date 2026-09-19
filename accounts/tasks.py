from celery import Task, shared_task

from config.enums import TaskPriority
from config.logging_config import logger
from notifications.enums import ChannelEnum
from notifications.services import send_email, send_sms
from notifications.sms import SmsRateLimitedError, SmsUnrecoverableError


@shared_task(
    bind=True,
    queue=TaskPriority.HIGH,
    autoretry_for=(Exception,),
    dont_autoretry_for=(SmsUnrecoverableError,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=5,
)
def send_magic_link_message(
    self: Task,
    *,
    account_uuid: str,
    channel: str,
    destination: str,
    subject: str,
    body: str,
    html: str,
    from_name: str,
    from_email: str,
    reply_to: str,
    sms_sender_id: str,
) -> None:
    """Sends a magic-link sign-in email/SMS.

    Always TaskPriority.HIGH - a human is sitting on the sign-in page
    waiting for this, unlike an ordinary notification (see
    config.enums.TaskPriority). retry_backoff_max is far shorter than
    notifications.tasks.send_message's own (60s, not 600s) for the same
    reason: a sign-in link retried 10 minutes from now is as good as
    useless to someone waiting on it right now. Takes the already-
    rendered subject/body/html rather than an occurrence/message id -
    unlike a real notification, there's no Message row behind this (see
    AGENTS.md's note on the magic-link flow bypassing Message entirely).
    """
    logger.debug(
        "send_magic_link_message starting",
        account=account_uuid,
        channel=channel,
        attempt=self.request.retries + 1,
    )
    try:
        if channel == ChannelEnum.EMAIL:
            send_email(
                to=destination,
                subject=subject,
                body=body,
                html=html or None,
                event_type="magic_link",
                from_name=from_name,
                from_email=from_email,
                reply_to=reply_to,
            )
        else:
            send_sms(
                to=destination,
                body=body,
                event_type="magic_link",
                sender_id=sms_sender_id,
            )
    except SmsUnrecoverableError as exc:
        logger.error(
            "magic link send failed permanently, not retrying",
            account=account_uuid,
            channel=channel,
            exc_info=exc,
        )
        raise
    except SmsRateLimitedError as exc:
        # Expected to resolve within a second or two - see
        # notifications.tasks.send_message's own identical branch.
        logger.debug(
            "magic link send deferred by sns rate limit",
            account=account_uuid,
            attempt=self.request.retries + 1,
            exc_info=exc,
        )
        raise
    except Exception as exc:
        logger.warning(
            "magic link send failed, will retry",
            account=account_uuid,
            channel=channel,
            attempt=self.request.retries + 1,
            exc_info=exc,
        )
        raise

    logger.info("magic link message sent", account=account_uuid, channel=channel)
