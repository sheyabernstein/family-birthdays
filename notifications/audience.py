"""Who gets notified about a given event, and on which channels.

Resolution order, most specific wins:
1. A person/union-specific NotificationPreference row - always decisive,
   whichever way it points. This is the escape hatch for "immediate
   family only, but I also want this one cousin" or "everyone, but not
   this one person".
2. A whole-event-type row (person and union both null). "muted" and
   "subscribed" are decisive; "immediate_family_only" defers to
   is_immediate_family() to decide per person/union.
3. EventType.default_opt_in - the family-wide default for this event
   type when the account hasn't said anything about it at all.

The account-wide channel toggle (Account.email_notifications_enabled
etc.) is a separate, coarser gate checked by available_channels() - it
turns a channel off entirely regardless of any of the above.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.db import models

from accounts.models import Account
from family.models import Person, Union
from notifications.enums import ChannelEnum
from notifications.models import EventType, NotificationPreference
from tenants.models import Family

_CHANNEL_SPECS = [
    (ChannelEnum.EMAIL, "email_notifications_enabled", lambda account: account.email),
    (ChannelEnum.SMS, "sms_notifications_enabled", lambda account: account.phone),
]


def available_channels(account: Account) -> list[tuple[str, str]]:
    """(channel, destination) pairs the account could ever be notified on at all.

    Has a destination on file and isn't switched off globally. Ignores
    preferences; use preference_status() for that.
    """
    result: list[tuple[str, str]] = []
    for channel, enabled_attr, destination_fn in _CHANNEL_SPECS:
        if not getattr(account, enabled_attr):
            continue
        destination = destination_fn(account)
        if destination:
            result.append((channel, destination))
    return result


def _viewer_person(account: Account, *, person: Person | None, union: Union | None) -> Person | None:
    """The account's own Person record in whichever family this event belongs to.

    An account can be linked to a Person in more than one family (e.g. an
    in-law also has a row in their birth family), so this resolves
    per-event rather than assuming a single identity.
    """
    if person is not None:
        family_ids = [person.family_id]
    else:
        family_ids = [union.person_a.family_id, union.person_b.family_id]
    return account.people.filter(family_id__in=family_ids).first()


def _is_spouse(a: Person, b: Person) -> bool:
    return Union.objects.filter(
        (models.Q(person_a=a, person_b=b) | models.Q(person_a=b, person_b=a)),
        status=Union.Status.MARRIED,
    ).exists()


def is_immediate_family(
    viewer: Person, subject: Person, *, spouse_check: Callable[[Person, Person], bool] = _is_spouse
) -> bool:
    """Spouse, parent, child, or sibling - the fixed, non-configurable definition of "immediate family".

    Used for the immediate_family_only preference. Everyone else
    (grandparents, cousins, in-laws beyond a spouse, ...) is "wider
    family" and can only be reached via an explicit per-person override.

    `spouse_check` defaults to a live DB lookup (_is_spouse), but callers
    resolving this for many (account, subject) pairs at once - see
    PreferenceResolver below - pass in a cache-backed version instead, so
    this stays a single, shared definition of "immediate family" either way.
    """
    if viewer.id == subject.id:
        return True
    if subject.father_id == viewer.id or subject.mother_id == viewer.id:
        return True  # subject is viewer's child
    if viewer.father_id == subject.id or viewer.mother_id == subject.id:
        return True  # subject is viewer's parent
    if viewer.father_id is not None and viewer.father_id in (subject.father_id, subject.mother_id):
        return True  # shares a father with subject - sibling (incl. half-sibling)
    if viewer.mother_id is not None and viewer.mother_id in (subject.father_id, subject.mother_id):
        return True  # shares a mother with subject - sibling (incl. half-sibling)
    return spouse_check(viewer, subject)


def _in_immediate_family(account: Account, *, person: Person | None, union: Union | None) -> bool:
    viewer = _viewer_person(account, person=person, union=union)
    if viewer is None:
        # Can't place this account in the family tree at all (e.g. an
        # admin-only login) - "immediate family only" can't be satisfied,
        # so fail closed rather than guessing.
        return False
    if person is not None:
        return is_immediate_family(viewer, person)
    return is_immediate_family(viewer, union.person_a) or is_immediate_family(viewer, union.person_b)


@dataclass
class PreferenceStatus:
    """Enough detail for the UI to explain *why* someone is or isn't subscribed, not just whether they are."""

    subscribed: bool
    # What's actually driving the result, for display purposes:
    # "specific_subscribed" / "specific_muted" - a person/union override
    # "type_subscribed" / "type_muted" - a whole-type row
    # "type_immediate_only" - a whole-type immediate_family_only row
    #   (in_immediate_family tells you which way it landed)
    # "default" - nothing set, using EventType.default_opt_in
    reason: str
    in_immediate_family: bool | None = None


def _preference_status_from_rows(
    *,
    specific: NotificationPreference | None,
    whole: NotificationPreference | None,
    event_type: EventType,
    in_family_fn: Callable[[], bool],
) -> PreferenceStatus:
    """The actual three-tier decision, shared by preference_status and PreferenceResolver.

    Both already have `specific`/`whole` in hand (one query each for the
    plain function; a dict lookup for the resolver) - this is just what to
    do with them, kept in one place so the two never drift apart.
    """
    if specific is not None:
        subscribed = specific.state == NotificationPreference.State.SUBSCRIBED
        return PreferenceStatus(
            subscribed=subscribed, reason="specific_subscribed" if subscribed else "specific_muted"
        )

    if whole is None:
        return PreferenceStatus(subscribed=event_type.default_opt_in, reason="default")

    if whole.state == NotificationPreference.State.MUTED:
        return PreferenceStatus(subscribed=False, reason="type_muted")
    if whole.state == NotificationPreference.State.SUBSCRIBED:
        return PreferenceStatus(subscribed=True, reason="type_subscribed")

    in_family = in_family_fn()
    return PreferenceStatus(subscribed=in_family, reason="type_immediate_only", in_immediate_family=in_family)


def preference_status(
    account: Account,
    event_type: EventType,
    *,
    person: Person | None = None,
    union: Union | None = None,
    channel: str,
) -> PreferenceStatus:
    specific_filter = models.Q(person=person) if person is not None else models.Q(union=union)
    specific = (
        NotificationPreference.objects.filter(account=account, event_type=event_type, channel=channel)
        .filter(specific_filter)
        .first()
    )
    whole = None
    if specific is None:
        whole = NotificationPreference.objects.filter(
            account=account, event_type=event_type, channel=channel, person__isnull=True, union__isnull=True
        ).first()
    return _preference_status_from_rows(
        specific=specific,
        whole=whole,
        event_type=event_type,
        in_family_fn=lambda: _in_immediate_family(account, person=person, union=union),
    )


def channels_for_account(
    account: Account, event_type: EventType, *, person: Person | None = None, union: Union | None = None
) -> list[tuple[str, str]]:
    """(channel, destination) pairs this account would actually be notified on for this event.

    Applies the full preference resolution above.
    """
    return [
        (channel, destination)
        for channel, destination in available_channels(account)
        if preference_status(account, event_type, person=person, union=union, channel=channel).subscribed
    ]


class PreferenceResolver:
    """Batches preference + immediate-family lookups for a known, fixed set of accounts and families.

    preference_status()/channels_for_account() each do their own query (or
    two) per call, which is fine for a single lookup (e.g. one row on
    PersonDetailView) but turns into a real N+1 when resolving an audience
    across every account in a family, or every candidate occurrence on the
    Dashboard - see AGENTS.md's notes on this. This loads every relevant
    NotificationPreference row, viewer-Person, and married-couple pair for
    the given accounts/families in three queries total, then answers every
    subsequent preference_status()/channels_for_account() call from memory.

    Scope this to exactly the accounts/families a given call site actually
    needs - it isn't meant to be built once and reused globally.
    """

    def __init__(self, *, account_ids: Iterable[int], family_ids: Iterable[int]) -> None:
        account_ids = list(account_ids)
        family_ids = list(family_ids)

        self._specific: dict[tuple, NotificationPreference] = {}
        self._whole: dict[tuple, NotificationPreference] = {}
        for pref in NotificationPreference.objects.filter(account_id__in=account_ids):
            base_key = (pref.account_id, pref.event_type_id, pref.channel)
            if pref.person_id is None and pref.union_id is None:
                self._whole[base_key] = pref
            else:
                self._specific[(*base_key, pref.person_id, pref.union_id)] = pref

        self._viewer_by_account: dict[int, Person] = {
            person.account_id: person
            for person in Person.objects.filter(account_id__in=account_ids, family_id__in=family_ids)
        }
        self._spouse_pairs: set[frozenset[int]] = {
            frozenset((union.person_a_id, union.person_b_id))
            for union in Union.objects.filter(
                status=Union.Status.MARRIED,
                person_a__family_id__in=family_ids,
                person_b__family_id__in=family_ids,
            )
        }

    def _cached_spouse_check(self, a: Person, b: Person) -> bool:
        return frozenset((a.id, b.id)) in self._spouse_pairs

    def _in_immediate_family(self, account_id: int, *, person: Person | None, union: Union | None) -> bool:
        viewer = self._viewer_by_account.get(account_id)
        if viewer is None:
            return False
        if person is not None:
            return is_immediate_family(viewer, person, spouse_check=self._cached_spouse_check)
        return is_immediate_family(
            viewer, union.person_a, spouse_check=self._cached_spouse_check
        ) or is_immediate_family(viewer, union.person_b, spouse_check=self._cached_spouse_check)

    def preference_status(
        self,
        account: Account,
        event_type: EventType,
        *,
        person: Person | None = None,
        union: Union | None = None,
        channel: str,
    ) -> PreferenceStatus:
        base_key = (account.id, event_type.id, channel)
        specific = self._specific.get((*base_key, person.id if person else None, union.id if union else None))
        whole = self._whole.get(base_key)
        return _preference_status_from_rows(
            specific=specific,
            whole=whole,
            event_type=event_type,
            in_family_fn=lambda: self._in_immediate_family(account.id, person=person, union=union),
        )

    def channels_for_account(
        self,
        account: Account,
        event_type: EventType,
        *,
        person: Person | None = None,
        union: Union | None = None,
    ) -> list[tuple[str, str]]:
        return [
            (channel, destination)
            for channel, destination in available_channels(account)
            if self.preference_status(
                account, event_type, person=person, union=union, channel=channel
            ).subscribed
        ]


def resolve_audience(
    *, event_type: EventType, person: Person | None = None, union: Union | None = None
) -> list[tuple[Account, str, str]]:
    """(account, channel, destination) for everyone who should be notified about this event."""
    if person is not None:
        family_ids = [person.family_id]
    else:
        family_ids = [union.person_a.family_id, union.person_b.family_id]

    accounts = list(
        Account.objects.filter(family_memberships__family_id__in=family_ids, is_active=True).distinct()
    )
    resolver = PreferenceResolver(account_ids=[a.id for a in accounts], family_ids=family_ids)

    audience = []
    for account in accounts:
        for channel, destination in resolver.channels_for_account(
            account, event_type, person=person, union=union
        ):
            audience.append((account, channel, destination))
    return audience


def resolve_broadcast_audience(
    *, event_type: EventType, family: Family, people: Iterable[Person]
) -> list[tuple[Account, str, str]]:
    """(account, channel, destination) for everyone who should receive a Broadcast.

    Every account in the broadcast's own family (not, unlike
    resolve_audience, an in-law's other family reached through a Union;
    a Broadcast is scoped to exactly one family via Broadcast.family).

    With no people tied to the broadcast, this is just channels_for_account
    with person=union=None - preference_status()'s "whole event type" and
    "default" branches only ever look at (account, event_type, channel)
    anyway, so that's safe; a whole-type immediate_family_only row would
    hit _in_immediate_family(person=None, union=None), which can't resolve
    a subject to compare against - callers on the muting UI side keep this
    state unreachable for the Broadcast event type in the first place (see
    NotificationPreference.clean() and family.views.PersonDetailView).

    With one or more people tied, an account is included if it's
    subscribed with respect to *any* of them - e.g. an immediate-family-
    only subscriber gets it if they're immediate family of at least one
    tied person, even if not all of them.
    """
    accounts = list(Account.objects.filter(family_memberships__family=family, is_active=True).distinct())
    people = list(people)
    resolver = PreferenceResolver(account_ids=[a.id for a in accounts], family_ids=[family.id])

    audience: list[tuple[Account, str, str]] = []
    seen: set[tuple[int, str, str]] = set()
    for account in accounts:
        if people:
            channels: set[tuple[str, str]] = set()
            for person in people:
                channels.update(resolver.channels_for_account(account, event_type, person=person))
        else:
            channels = set(resolver.channels_for_account(account, event_type))

        for channel, destination in channels:
            key = (account.id, channel, destination)
            if key not in seen:
                seen.add(key)
                audience.append((account, channel, destination))
    return audience
