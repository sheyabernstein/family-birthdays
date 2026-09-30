"""Who gets notified about a given event, and on which channels.

Resolution order, most specific wins:
1. A person/union-specific NotificationPreference row - always decisive,
   whichever way it points. This is the escape hatch for "immediate
   family only, but I also want this one cousin" or "everyone, but not
   this one person".
2. A whole-event-type row (person and union both null). "muted" and
   "subscribed" are decisive; "immediate_family_only"/
   "direct_family_only" defer to is_immediate_family()/
   is_direct_family() to decide per person/union.
3. EventType.default_state - the family-wide default for this event
   type when the account hasn't said anything about it at all. Resolved
   through the exact same state-branching as an explicit whole-type row
   (see _preference_status_from_rows) - "nothing set" is just treated as
   an implicit row carrying default_state.

The account-wide channel toggle (Account.email_notifications_enabled
etc.) is a separate, coarser gate checked by available_channels() - it
turns a channel off entirely regardless of any of the above.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.db import models

from accounts.models import Account
from family.models import Person, Union
from family.relationships import (
    _ancestor_ids_for,
    _descendant_ids_for,
    is_direct_family,
    is_immediate_family,
    memoize_by_person,
    person_visible_to,
)
from notifications.enums import ChannelEnum
from notifications.models import EventType, NotificationPreference
from tenants.models import Family, FamilyMembership

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


def viewer_person(account: Account, *, person: Person | None, union: Union | None) -> Person | None:
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


def viewer_family_ids_for_union(account_ids: list[int], union: Union) -> dict[int, int]:
    """Batch form of viewer_person for a union, in one query instead of one per account.

    notifications.tasks.send_due_notifications needs "which side is this
    account on" for every recipient of a union-anchored occurrence (see
    Union.ordered_pair) - calling viewer_person per account there turned
    a same-cost-regardless-of-recipient-count render into one query per
    recipient. Only the two families a union could ever place someone on
    are candidates, so a single filter covers every account at once.

    An account missing from the returned dict couldn't be placed on
    either side (viewer_person would have returned None for it too) -
    callers already treat that the same way.
    """
    rows = Person.objects.filter(
        account_id__in=account_ids, family_id__in=[union.person_a.family_id, union.person_b.family_id]
    ).values_list("account_id", "family_id")
    return dict(rows)


def _in_immediate_family(account: Account, *, person: Person | None, union: Union | None) -> bool:
    if person is None and union is None:
        # No subject at all (a Broadcast with no tied people) - there's
        # nothing to be "immediate family of", so fail closed the same
        # way a viewer who can't be placed in the tree does below.
        return False
    viewer = viewer_person(account, person=person, union=union)
    if viewer is None:
        # Can't place this account in the family tree at all (e.g. an
        # admin-only login) - "immediate family only" can't be satisfied,
        # so fail closed rather than guessing.
        return False
    if person is not None:
        return is_immediate_family(viewer, person)
    return is_immediate_family(viewer, union.person_a) or is_immediate_family(viewer, union.person_b)


def _in_direct_family(account: Account, *, person: Person | None, union: Union | None) -> bool:
    if person is None and union is None:
        return False
    viewer = viewer_person(account, person=person, union=union)
    if viewer is None:
        return False
    if person is not None:
        return is_direct_family(viewer, person)
    return is_direct_family(viewer, union.person_a) or is_direct_family(viewer, union.person_b)


def _can_edit(account: Account, *, person: Person | None, union: Union | None) -> bool:
    family_ids = (
        [person.family_id] if person is not None else [union.person_a.family_id, union.person_b.family_id]
    )
    return FamilyMembership.objects.filter(
        account=account, family_id__in=family_ids, role__in=FamilyMembership.EDITOR_ROLES
    ).exists()


def _visible_to(account: Account, *, person: Person | None, union: Union | None) -> bool:
    """Person.visibility's own gate - a ceiling under every preference state above, not a replacement.

    No subject at all (a Broadcast with no tied people) has nothing to
    check visibility against, so it's always visible - see
    resolve_broadcast_audience's own docstring for why that combination
    is otherwise unreachable anyway. Owners/editors bypass this, same as
    everywhere else this app checks visibility (family.access).
    """
    if person is None and union is None:
        return True
    if _can_edit(account, person=person, union=union):
        return True
    viewer = viewer_person(account, person=person, union=union)
    if person is not None:
        return person_visible_to(viewer, person)
    return person_visible_to(viewer, union.person_a) or person_visible_to(viewer, union.person_b)


@dataclass
class PreferenceStatus:
    """Enough detail for the UI to explain *why* someone is or isn't subscribed, not just whether they are."""

    subscribed: bool
    # What's actually driving the result, for display purposes:
    # "not_visible" - Person.visibility ruled this out before any
    #   preference was even consulted - a ceiling, not one more state
    # "specific_subscribed" / "specific_muted" - a person/union override
    # "type_subscribed" / "type_muted" - an explicit whole-type row
    # "type_immediate_only" - an explicit whole-type immediate_family_only row
    #   (in_immediate_family tells you which way it landed)
    # "type_direct_family_only" - an explicit whole-type
    #   direct_family_only row
    # "default" - nothing set, using EventType.default_state (whichever
    #   of the above states that resolves to)
    reason: str
    in_immediate_family: bool | None = None


def _preference_status_from_rows(
    *,
    specific: NotificationPreference | None,
    whole: NotificationPreference | None,
    event_type: EventType,
    in_family_fn: Callable[[], bool],
    in_direct_family_fn: Callable[[], bool],
    visible_fn: Callable[[], bool],
) -> PreferenceStatus:
    """The actual state-branching decision, shared by preference_status and PreferenceResolver.

    Both already have `specific`/`whole` in hand (one query each for the
    plain function; a dict lookup for the resolver) - this is just what to
    do with them, kept in one place so the two never drift apart.

    "Nothing set at all" (whole is None) is treated as an implicit whole-
    type row carrying event_type.default_state, rather than its own
    separate branch - so a default_state of direct_family_only
    (Yahrzeit's own default) is resolved by the exact same
    is_direct_family check an explicit override would use, just tagged
    "default" instead of "type_direct_family_only" for display
    purposes.

    visible_fn is checked before any of that - Person.visibility is a
    ceiling on eligibility, not one more preference state, so even a
    specific per-account "subscribed" override can't see past it.
    """
    if not visible_fn():
        return PreferenceStatus(subscribed=False, reason="not_visible")

    if specific is not None:
        subscribed = specific.state == NotificationPreference.State.SUBSCRIBED
        return PreferenceStatus(
            subscribed=subscribed, reason="specific_subscribed" if subscribed else "specific_muted"
        )

    is_explicit = whole is not None
    state = whole.state if is_explicit else event_type.default_state

    if state == NotificationPreference.State.MUTED:
        return PreferenceStatus(subscribed=False, reason="type_muted" if is_explicit else "default")
    if state == NotificationPreference.State.SUBSCRIBED:
        return PreferenceStatus(subscribed=True, reason="type_subscribed" if is_explicit else "default")
    if state == NotificationPreference.State.IMMEDIATE_FAMILY_ONLY:
        in_family = in_family_fn()
        return PreferenceStatus(
            subscribed=in_family,
            reason="type_immediate_only" if is_explicit else "default",
            in_immediate_family=in_family,
        )

    if state == NotificationPreference.State.DIRECT_FAMILY_ONLY:
        in_direct_family = in_direct_family_fn()
        return PreferenceStatus(
            subscribed=in_direct_family, reason="type_direct_family_only" if is_explicit else "default"
        )

    # Not reachable through the model's own choices= validation, but
    # nothing at the DB level stops a raw-written or corrupted row from
    # holding something else - silently falling through to one of the
    # branches above would misresolve it instead of surfacing the
    # problem.
    raise ValueError(f"Unrecognized NotificationPreference state: {state!r}")


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
        in_direct_family_fn=lambda: _in_direct_family(account, person=person, union=union),
        visible_fn=lambda: _visible_to(account, person=person, union=union),
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
    NotificationPreference row, viewer-Person, married-couple pair, and
    editor/owner FamilyMembership for the given accounts/families in four
    queries total, then answers every subsequent preference_status()/
    channels_for_account() call from memory.

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
        # Person.visibility's own owners/editors-always-see-everyone bypass -
        # see family.access for the People list/tree side of the same rule.
        self._editor_account_ids: set[int] = set(
            FamilyMembership.objects.filter(
                account_id__in=account_ids, family_id__in=family_ids, role__in=FamilyMembership.EDITOR_ROLES
            ).values_list("account_id", flat=True)
        )
        # Either side in scope, not both - a Union can legitimately span
        # two Family tenants (the in-law marriage exception - see
        # family/access.py's own docstring), and "are these two people
        # married" doesn't depend on whether the *other* spouse's own
        # family happens to also be in this resolver's family_ids.
        # Requiring both sides used to silently treat a person as
        # spouseless whenever their spouse's family wasn't in scope.
        self._spouse_pairs: set[frozenset[int]] = {
            frozenset((union.person_a_id, union.person_b_id))
            for union in Union.objects.filter(
                models.Q(person_a__family_id__in=family_ids) | models.Q(person_b__family_id__in=family_ids),
                status=Union.Status.MARRIED,
            )
        }
        # Lazily filled, not precomputed like the two sets above - an
        # ancestor/descendant chain, or a person's own spouse lookup, is
        # per-viewer rather than a fixed-size per-pair set the way spouse
        # pairs are; each is still only ever computed once regardless of
        # how many subjects it ends up checked against.
        self._ancestor_ids = memoize_by_person(_ancestor_ids_for)
        self._descendant_ids = memoize_by_person(_descendant_ids_for)
        self._spouses_by_person: dict[int, list[Person]] = {}
        self._person_by_id: dict[int, Person] = {}

    def _cached_spouse_check(self, a: Person, b: Person) -> bool:
        return frozenset((a.id, b.id)) in self._spouse_pairs

    def _in_immediate_family(self, account_id: int, *, person: Person | None, union: Union | None) -> bool:
        if person is None and union is None:
            # No subject at all (a Broadcast with no tied people) - see
            # the module-level _in_immediate_family's own comment.
            return False
        viewer = self._viewer_by_account.get(account_id)
        if viewer is None:
            return False
        if person is not None:
            return is_immediate_family(viewer, person, spouse_check=self._cached_spouse_check)
        return is_immediate_family(
            viewer, union.person_a, spouse_check=self._cached_spouse_check
        ) or is_immediate_family(viewer, union.person_b, spouse_check=self._cached_spouse_check)

    def _cached_spouses_of(self, person: Person) -> list[Person]:
        """Reuses the already-loaded _spouse_pairs (no extra query for the pairing itself).

        Only fetches the actual Person rows on first request per person,
        not up front - most resolved subjects never need their spouse
        looked up at all (only is_direct_family does).
        """
        spouses = self._spouses_by_person.get(person.id)
        if spouses is None:
            partner_ids = [
                other
                for pair in self._spouse_pairs
                if person.id in pair
                for other in pair
                if other != person.id
            ]
            missing_ids = [pid for pid in partner_ids if pid not in self._person_by_id]
            if missing_ids:
                for fetched in Person.objects.filter(pk__in=missing_ids):
                    self._person_by_id[fetched.id] = fetched
            spouses = [self._person_by_id[pid] for pid in partner_ids if pid in self._person_by_id]
            self._spouses_by_person[person.id] = spouses
        return spouses

    def _in_direct_family(self, account_id: int, *, person: Person | None, union: Union | None) -> bool:
        if person is None and union is None:
            return False
        viewer = self._viewer_by_account.get(account_id)
        if viewer is None:
            return False
        kwargs = {
            "spouse_check": self._cached_spouse_check,
            "ancestor_ids_fn": self._ancestor_ids,
            "descendant_ids_fn": self._descendant_ids,
            "spouses_of_fn": self._cached_spouses_of,
        }
        if person is not None:
            return is_direct_family(viewer, person, **kwargs)
        return is_direct_family(viewer, union.person_a, **kwargs) or is_direct_family(
            viewer, union.person_b, **kwargs
        )

    def _person_visible(self, viewer: Person | None, subject: Person) -> bool:
        return person_visible_to(
            viewer,
            subject,
            spouse_check=self._cached_spouse_check,
            ancestor_ids_fn=self._ancestor_ids,
            descendant_ids_fn=self._descendant_ids,
            spouses_of_fn=self._cached_spouses_of,
        )

    def _visible_to(self, account_id: int, *, person: Person | None, union: Union | None) -> bool:
        if person is None and union is None:
            return True
        if account_id in self._editor_account_ids:
            return True
        viewer = self._viewer_by_account.get(account_id)
        if person is not None:
            return self._person_visible(viewer, person)
        return self._person_visible(viewer, union.person_a) or self._person_visible(viewer, union.person_b)

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
            in_direct_family_fn=lambda: self._in_direct_family(account.id, person=person, union=union),
            visible_fn=lambda: self._visible_to(account.id, person=person, union=union),
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
    anyway, so that's safe. A whole-type immediate_family_only row (a
    real, reachable case - NotificationPreference.clean() only blocks a
    person/union-scoped Broadcast row, not this whole-type state) has no
    subject to compare against, so _in_immediate_family/_in_direct_family
    both fail closed (excluded) when person and union are both None,
    rather than guessing.

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
