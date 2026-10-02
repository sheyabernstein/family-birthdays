"""Batched email/SMS notifications for Suggestion review activity.

Two notifications, both batched rather than sent one-per-suggestion:

- Owners/editors get a digest of newly pending suggestions to review.
- A submitter gets a digest of their own suggestions that were just
  approved/rejected.

Both go through the real notifications.models.Message/notifications.
tasks.send_message pipeline, not a bespoke one-off send - once a real
SMS leg is in play (see the channel-preference bullet below),
hand-rolling a second retrying task would either duplicate send_message's
already-tested AWS SNS global rate limiting and its
SmsRateLimitedError/SmsUnrecoverableError handling, or (more likely)
silently miss it. Message.direct_family (see that field's own docstring)
is what makes this possible without an Occurrence/Broadcast anchor -
there's no EventType/NotificationPreference row behind these, though:
unlike every other event type, getting told a suggestion you submitted
was reviewed, or that your family has suggestions waiting, isn't a
subscription preference, it's a direct consequence of something the
recipient did (submitted something) or their own role (owner/editor), so
these are never mute-able via My Notifications.

Email is preferred; SMS is a fallback for an account with no email on
file (or that's disabled email entirely) - notifications.audience.
available_channels(account, family=...) already returns (channel,
destination) pairs ordered email-first and filtered down to "has a
destination, hasn't switched that channel off, and the family hasn't
disabled it tenant-wide", so taking its first entry gives exactly that
preference order with no bespoke logic of its own. An SMS digest is a
short count-and-link, never the full per-suggestion breakdown the email
gets.

Batching is a claim-then-send sweep, the same shape notifications.tasks
already uses for Occurrence/Broadcast (see send_due_notifications/
send_due_broadcasts) - with one twist those don't need: the claim value
here (Suggestion.pending_message/resolved_message) is itself a Message
row, which doesn't exist until after it's been rendered and inserted, so
"claim" can't happen first the way a plain boolean/timestamp flip can.
Each batch builds its Message row(s) *inside* the same transaction.atomic()
block as the claiming update() instead, then checks that update()'s own
affected-row count against the full size of the batch before anything
leaves the transaction: an exact match means this sweep genuinely won
the claim for every suggestion the digest it already rendered talks
about, so the block commits and the dispatch loop below sends for real.
Anything less - including a *partial* match, not just zero - means an
overlapping sweep claimed some (not necessarily all) of this same batch
between this run's own read above and this update() (another worker, a
previous run still finishing, or just a new suggestion arriving between
the two runs' reads), so the digest content already rendered from this
run's now-stale `suggestions` list can't be trusted - it may re-mention
something the other sweep already claimed and already sent its own
digest for. transaction.set_rollback(True) discards the Message rows
just built and nothing is dispatched in that case - never two sweeps
independently emailing/texting overlapping content.
send_suggestion_digests runs on Celery Beat
every 30 minutes (config.settings.CELERY_BEAT_SCHEDULE) - frequent enough
that nobody's waiting long, but batching a family member adding/
processing a handful of suggestions in one sitting into a single message
rather than one per suggestion.
"""

from collections.abc import Callable
from itertools import groupby

from celery import shared_task
from django.db import transaction
from django.template.loader import render_to_string

from accounts.models import Account
from config.enums import TaskPriority
from family.models import Suggestion
from notifications.audience import available_channels
from notifications.enums import ChannelEnum
from notifications.helpers import html_to_plain_text, truncate_for_sms
from notifications.models import Message
from notifications.services import absolute_url
from notifications.tasks import send_message
from tenants.models import Family, FamilyMembership

_SUGGESTION_SELECT_RELATED = (
    "family",
    "submitted_by",
    "target_person",
    "target_union__person_a",
    "target_union__person_b",
    "proposed_person_a",
    "proposed_person_b",
    "link_with",
    "resulting_person",
)


def _preferred_channel(account: Account, *, family: Family) -> tuple[str, str] | None:
    """The one (channel, destination) to notify this account on, email-first - or None if neither is viable."""
    channels = available_channels(account, family=family)
    return channels[0] if channels else None


def _render_digest(*, html_template: str, sms_template: str, context: dict) -> tuple[str, str, str]:
    """Renders (email html, email plain-text, sms text) once, shared across every recipient of one batch."""
    html = render_to_string(html_template, context)
    sms_text = truncate_for_sms(render_to_string(sms_template, context).strip())
    return html, html_to_plain_text(html), sms_text


def _build_message(
    *,
    family: Family,
    account: Account,
    channel: str,
    destination: str,
    subject: str,
    email_html: str,
    email_body: str,
    sms_text: str,
) -> Message:
    if channel == ChannelEnum.EMAIL:
        return Message(
            direct_family=family,
            account=account,
            channel=channel,
            destination=destination,
            subject=subject,
            body=email_body,
            html_body=email_html,
        )
    return Message(
        direct_family=family, account=account, channel=channel, destination=destination, body=sms_text
    )


def _claim_and_dispatch(
    *, suggestion_ids: list[int], claim_field: str, persist: Callable[[], list[Message]]
) -> None:
    """Persists Message row(s) via persist(), atomically claims every suggestion_id onto the first one, then dispatches.

    Shared by _send_pending_review_digests/_send_resolved_digests -
    identical claim/rollback/dispatch shape either way, just a different
    claim_field ("pending_message"/"resolved_message") and a different
    persist() (bulk_create for the pending-review fan-out, a single
    save() for the one-recipient resolved case - see this module's own
    docstring for why persist() has to run *inside* the same
    transaction.atomic() as the claim, not before it).

    persist()'s own return value is never used for anything but claiming
    and dispatching - callers that need the created Message(s) for
    something else (there currently aren't any) would need their own
    variant.
    """
    with transaction.atomic():
        created = persist()
        claimed = Suggestion.objects.filter(pk__in=suggestion_ids, **{f"{claim_field}__isnull": True}).update(
            **{claim_field: created[0]}
        )
        if claimed != len(suggestion_ids):
            # An overlapping sweep already claimed some (not necessarily
            # all) of this exact batch between our own read above and
            # this update() - e.g. it committed its own claim of
            # suggestions [1,2,3] moments after we read [1,2,3,4] (a 4th
            # arrived in between). A partial count here is just as
            # unsafe to proceed on as a zero count: the digest content
            # already rendered by the caller was built from the full,
            # now-stale suggestion list, so sending it would re-notify
            # about whatever the other sweep just claimed and already
            # sent its own digest for. Roll back the Message row(s) we
            # just built rather than risk that.
            transaction.set_rollback(True)
            return
    for message in created:
        send_message.delay(message.pk)


def _send_pending_review_digests() -> None:
    pending = list(
        Suggestion.objects.filter(status=Suggestion.Status.PENDING, pending_message__isnull=True)
        .select_related(*_SUGGESTION_SELECT_RELATED)
        .order_by("family_id")
    )
    review_url = absolute_url("family:suggestions")
    for _family_id, group in groupby(pending, key=lambda suggestion: suggestion.family_id):
        suggestions = list(group)
        family: Family = suggestions[0].family
        recipients = [
            (account, *channel_destination)
            for account in Account.objects.filter(
                family_memberships__family=family,
                family_memberships__role__in=FamilyMembership.EDITOR_ROLES,
                is_active=True,
            ).distinct()
            if (channel_destination := _preferred_channel(account, family=family)) is not None
        ]
        if not recipients:
            continue

        count = len(suggestions)
        subject = f"{count} suggestion{'s' if count != 1 else ''} to review"
        email_html, email_body, sms_text = _render_digest(
            html_template="family/email/suggestion_pending_review.html",
            sms_template="family/sms/suggestion_pending_review.txt",
            context={
                "suggestions": suggestions,
                "review_url": review_url,
                "count": count,
                "family_name": family.name,
            },
        )

        messages = [
            _build_message(
                family=family,
                account=account,
                channel=channel,
                destination=destination,
                subject=subject,
                email_html=email_html,
                email_body=email_body,
                sms_text=sms_text,
            )
            for account, channel, destination in recipients
        ]
        _claim_and_dispatch(
            suggestion_ids=[suggestion.pk for suggestion in suggestions],
            claim_field="pending_message",
            persist=lambda messages=messages: Message.objects.bulk_create(messages),
        )


def _send_resolved_digests() -> None:
    resolved = list(
        Suggestion.objects.filter(
            status__in=[Suggestion.Status.APPROVED, Suggestion.Status.REJECTED],
            resolved_message__isnull=True,
        )
        .select_related(*_SUGGESTION_SELECT_RELATED)
        .order_by("family_id", "submitted_by_id")
    )
    review_url = absolute_url("family:suggestions")
    for (_family_id, _submitted_by_id), group in groupby(
        resolved, key=lambda suggestion: (suggestion.family_id, suggestion.submitted_by_id)
    ):
        suggestions = list(group)
        family: Family = suggestions[0].family
        submitted_by = suggestions[0].submitted_by
        if not submitted_by.is_active:
            continue
        channel_destination = _preferred_channel(submitted_by, family=family)
        if channel_destination is None:
            continue
        channel, destination = channel_destination

        count = len(suggestions)
        subject = f"Your suggestion{'s' if count != 1 else ''} {'were' if count != 1 else 'was'} reviewed"
        email_html, email_body, sms_text = _render_digest(
            html_template="family/email/suggestion_resolved.html",
            sms_template="family/sms/suggestion_resolved.txt",
            context={
                "suggestions": suggestions,
                "review_url": review_url,
                "count": count,
                "family_name": family.name,
            },
        )

        message = _build_message(
            family=family,
            account=submitted_by,
            channel=channel,
            destination=destination,
            subject=subject,
            email_html=email_html,
            email_body=email_body,
            sms_text=sms_text,
        )

        def _save_and_wrap(message: Message = message) -> list[Message]:
            message.save()
            return [message]

        _claim_and_dispatch(
            suggestion_ids=[suggestion.pk for suggestion in suggestions],
            claim_field="resolved_message",
            persist=_save_and_wrap,
        )


@shared_task(queue=TaskPriority.LOW)
def send_suggestion_digests() -> None:
    """Claims newly-pending and newly-resolved suggestions and sends one digest per recipient.

    Runs on Celery Beat every 30 minutes - see config.settings.
    CELERY_BEAT_SCHEDULE and this module's own docstring for why a
    periodic claim-then-send sweep, not a per-suggestion task.
    """
    _send_pending_review_digests()
    _send_resolved_digests()
