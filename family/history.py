"""Builds the "History" table on a person's detail page.

Reads django-reversion's own Version/Revision records (see AGENTS.md -
every mutable model here is already registered with reversion, so this
is just reading that data, not a second audit log to keep in sync).

Version.field_dict gives a plain {field_name: raw_value} snapshot per
save; diffing two consecutive snapshots for the same object is all a
"what changed" table needs. FK fields come through as "<field>_id" with
a raw pk, so those are resolved to the related object's own __str__ via
Model._meta introspection - generic across Person/Union, not hardcoded
per field, since both models' FKs (father/mother/account, person_a/
person_b) work the same way.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from django.db.models import Model
from reversion.models import Version

from accounts.models import Account
from family.models import Person, Union

HISTORY_LIMIT = 20

# Never meaningful in an audit trail - either an opaque id or already
# redundant with the revision's own timestamp.
_HIDDEN_FIELDS = {"id", "uuid", "created_at", "updated_at"}


@dataclasses.dataclass
class HistoryEventChange:
    """A single changed field, rendered as "label: old → new".

    Or, when old_display/new_display are both left None, a label-only
    line (the "Record created"/"Earliest recorded version" marker has
    nothing to diff against).
    """

    label: str
    old_display: str | None = None
    new_display: str | None = None


@dataclasses.dataclass
class HistoryEvent:
    when: dt.datetime
    who: Account | None
    subject_label: str
    changes: list[HistoryEventChange]


def _field_label(model: type[Model], field_name: str) -> str:
    lookup_name = field_name.removesuffix("_id")
    try:
        return str(model._meta.get_field(lookup_name).verbose_name)
    except Exception:
        return field_name


def _related_field(model: type[Model], field_name: str) -> Any | None:
    """The relation field a "<field>_id" version key came from, or None if it isn't one."""
    if not field_name.endswith("_id"):
        return None
    try:
        field = model._meta.get_field(field_name.removesuffix("_id"))
    except Exception:
        return None
    return field if field.is_relation else None


def _field_display(
    model: type[Model], field_name: str, value: Any, related_cache: dict[tuple[type[Model], Any], Model]
) -> str:
    # Blank text fields (CharField/TextField's blank=True, not null=True -
    # notes, nicknames, ...) store "" rather than None, so both need the
    # same placeholder to actually be visible in a diff.
    if value is None or value == "":
        return ""
    field = _related_field(model, field_name)
    if field is not None:
        related = related_cache.get((field.related_model, value))
        return str(related) if related is not None else f"#{value} (deleted)"
    return str(value)


def _created_change(
    model: type[Model], fields: dict[str, Any], version_when: dt.datetime
) -> HistoryEventChange:
    return HistoryEventChange(label=_created_label(model, fields, version_when))


def _created_label(model: type[Model], fields: dict[str, Any], version_when: dt.datetime) -> str:
    """Labels the first tracked version "Record created" or "Earliest recorded version".

    Only "Record created" when this version's own timestamp actually
    matches the object's created_at - reversion was added partway
    through this project's life, and bulk imports run outside any
    request (so they're never tracked at all), so the first *tracked*
    version for an older record is usually just "whenever someone first
    happened to save it after tracking began", not its real origin.
    Models with no created_at field (Union) can't make this distinction,
    so they keep the plain label.
    """
    try:
        model._meta.get_field("created_at")
    except Exception:
        return "Record created"
    created_at = fields.get("created_at")
    if created_at is not None and created_at >= version_when - dt.timedelta(seconds=5):
        return "Record created"
    return "Earliest recorded version"


def _events_for_object(obj: Model, *, subject_label: str) -> list[HistoryEvent]:
    versions = list(
        Version.objects.get_for_object(obj)
        .select_related("revision", "revision__user")
        .order_by("revision__date_created")
    )
    model = type(obj)

    # Pass 1: walk the diffs and collect every related (model, pk) a changed
    # FK field will need displayed, without resolving any of them yet - so
    # each distinct related model can be fetched in one batched query below
    # instead of one query per changed FK field per version (the actual
    # cost scaled with edit-history length before this change - see
    # AGENTS.md's note on this).
    raw_created: list[tuple[dt.datetime, Account | None, dict[str, Any]]] = []
    raw_changes: list[tuple[dt.datetime, Account | None, list[tuple[str, Any, Any]]]] = []
    needed: dict[type[Model], set[Any]] = defaultdict(set)
    previous_fields: dict[str, Any] | None = None

    for version in versions:
        fields = version.field_dict
        who = version.revision.user
        when = version.revision.date_created

        if previous_fields is None:
            raw_created.append((when, who, fields))
            previous_fields = fields
            continue

        diffs = []
        for key, new_value in fields.items():
            if key in _HIDDEN_FIELDS:
                continue
            old_value = previous_fields.get(key)
            if old_value == new_value:
                continue
            diffs.append((key, old_value, new_value))
            field = _related_field(model, key)
            if field is not None:
                for related_value in (old_value, new_value):
                    if related_value not in (None, ""):
                        needed[field.related_model].add(related_value)

        if diffs:
            raw_changes.append((when, who, diffs))
        previous_fields = fields

    related_cache: dict[tuple[type[Model], Any], Model] = {
        (related_model, related_obj.pk): related_obj
        for related_model, pks in needed.items()
        for related_obj in related_model.objects.filter(pk__in=pks)
    }

    events: list[HistoryEvent] = [
        HistoryEvent(
            when=when, who=who, subject_label=subject_label, changes=[_created_change(model, fields, when)]
        )
        for when, who, fields in raw_created
    ]
    events.extend(
        HistoryEvent(
            when=when,
            who=who,
            subject_label=subject_label,
            changes=[
                HistoryEventChange(
                    label=_field_label(model, key),
                    old_display=_field_display(model, key, old_value, related_cache),
                    new_display=_field_display(model, key, new_value, related_cache),
                )
                for key, old_value, new_value in diffs
            ],
        )
        for when, who, diffs in raw_changes
    )
    return events


def person_history(person: Person, *, unions: Iterable[Union]) -> list[HistoryEvent]:
    """Builds the combined history for a person and their unions.

    Most-recent-first, capped to HISTORY_LIMIT - the Django admin's own
    per-object history view (owner/editor accounts aren't staff, so it's
    not linked from here, but a real site admin can still see the
    uncapped record there) is the place for anything beyond that.
    """
    events = _events_for_object(person, subject_label="This person")
    for union in unions:
        other = getattr(union, "other_person", None) or union.other(person)
        events.extend(_events_for_object(union, subject_label=f"Marriage to {other.display_name}"))
    events.sort(key=lambda event: event.when, reverse=True)
    return events[:HISTORY_LIMIT]
