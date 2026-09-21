from django.db.models.signals import post_save
from django.dispatch import receiver

from config.logging_config import logger
from notifications.models import EventType
from notifications.tasks import compute_occurrences


@receiver(post_save, sender=EventType)
def _recompute_occurrences_after_event_type_save(
    sender: type[EventType], instance: EventType, **kwargs: object
) -> None:
    """Queues the full occurrence sweep after any EventType save.

    Same "always recompute, don't diff which fields changed" policy as
    family.signals' own Person/Union receivers - notify_days_before,
    anchor, applies_to_union, and recurs all change scheduling behavior
    (always_schedule too, via the tracked-gate in notifications.tasks),
    and a "which fields actually matter" safelist would just be the same
    bug-prone bookkeeping those receivers already reject, moved one
    layer down.

    Unlike Person/Union, an EventType is usually a global row
    (family=None) shared across every family, so there's no cheap
    per-subject scope to recompute the way compute_occurrences_for_
    person/_union do - this queues the same compute_occurrences() sweep
    that already runs nightly, just immediately rather than waiting for
    the schedule, instead of doing the equivalent work synchronously in
    the save's own request/response cycle (a real risk for a large
    ledger, since a global EventType change reaches every family's
    people). Caught and logged, not raised, for the same reason as
    family.signals: a failure to queue shouldn't turn an otherwise-
    successful save into a 500 - the nightly sweep is still the
    self-healing backstop regardless.
    """
    try:
        compute_occurrences.delay()
    except Exception as exc:
        logger.warning(
            "failed to queue occurrence recompute after event type save",
            event_type=instance.code,
            exc_info=exc,
        )
