from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from config.logging_config import logger
from family.models import Person, Union
from notifications.tasks import compute_occurrences_for_person, compute_occurrences_for_union


@receiver(post_save, sender=Person)
def _recompute_person_occurrences(sender: type[Person], instance: Person, **kwargs: object) -> None:
    """Keeps a person's (and their unions') occurrences current on every save.

    Single source of truth for "an edit needs its occurrences recomputed
    right away" - covers the real forms, the Django admin, a future API,
    and any one-off script, all for free, rather than requiring every
    write path to remember to call compute_occurrences_for_person itself.
    Always recomputes rather than diffing which fields changed - the
    fields that matter span two models (a Person's own death date
    affects their Union's eligibility too), so a "relevant fields"
    safelist would just be the same thing this signal replaces, moved
    one layer down. Doesn't fire for bulk_create()/bulk_update()/
    QuerySet.update() - those skip signals entirely; the nightly
    compute_occurrences() sweep is still the self-healing backstop for
    any write path that bypasses .save().

    Caught and logged, not raised - a recompute failure shouldn't turn an
    otherwise-successful save into a 500; the nightly sweep will pick it
    up regardless.
    """
    try:
        compute_occurrences_for_person(instance)
        for union in Union.objects.filter(models.Q(person_a=instance) | models.Q(person_b=instance)):
            compute_occurrences_for_union(union)
    except Exception as exc:
        logger.warning("occurrence recompute failed after person save", person_id=instance.pk, exc_info=exc)


@receiver(post_save, sender=Union)
def _recompute_union_occurrences(sender: type[Union], instance: Union, **kwargs: object) -> None:
    try:
        compute_occurrences_for_union(instance)
    except Exception as exc:
        logger.warning("occurrence recompute failed after union save", union_id=instance.pk, exc_info=exc)
