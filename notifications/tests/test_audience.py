import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Account
from family.models import Person, Union
from notifications.audience import (
    PreferenceResolver,
    channels_for_account,
    preference_status,
    resolve_audience,
    resolve_broadcast_audience,
)
from notifications.models import EventType, NotificationPreference
from notifications.tests.conftest import member as _member
from tenants.models import Family, FamilyMembership

pytestmark = pytest.mark.django_db


def _preference(account, event_type, state, *, person=None, union=None, channel="email"):
    return NotificationPreference.objects.create(
        account=account, event_type=event_type, person=person, union=union, channel=channel, state=state
    )


def test_default_is_subscribed_when_nothing_is_set(family, birthday_event_type):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = _member(family)

    status = preference_status(account, birthday_event_type, person=person, channel="email")

    assert status.subscribed is True
    assert status.reason == "default"
    assert channels_for_account(account, birthday_event_type, person=person) == [("email", account.email)]


def test_default_follows_event_type_default_state(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = _member(family)
    event_type = EventType.objects.create(
        family=family,
        code="opt-in-thing",
        name="Opt In Thing",
        default_state=NotificationPreference.State.MUTED,
    )

    status = preference_status(account, event_type, person=person, channel="email")

    assert status.subscribed is False
    assert status.reason == "default"


def test_specific_mute_wins_over_default(family, birthday_event_type):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = _member(family)
    _preference(account, birthday_event_type, NotificationPreference.State.MUTED, person=person)

    status = preference_status(account, birthday_event_type, person=person, channel="email")

    assert status.subscribed is False
    assert status.reason == "specific_muted"

    other_person = Person.objects.create(family=family, first_name_en="Other", last_name_en="Person")
    assert (
        preference_status(account, birthday_event_type, person=other_person, channel="email").subscribed
        is True
    )


def test_whole_type_mute_applies_to_everyone(family, birthday_event_type):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = _member(family)
    _preference(account, birthday_event_type, NotificationPreference.State.MUTED)

    assert (
        preference_status(account, birthday_event_type, person=person, channel="email").reason == "type_muted"
    )
    other_person = Person.objects.create(family=family, first_name_en="Other", last_name_en="Person")
    assert (
        preference_status(account, birthday_event_type, person=other_person, channel="email").subscribed
        is False
    )


def test_specific_subscribe_overrides_a_whole_type_mute(family, birthday_event_type):
    # The escape hatch: muted for everyone, except this one person.
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = _member(family)
    _preference(account, birthday_event_type, NotificationPreference.State.MUTED)
    _preference(account, birthday_event_type, NotificationPreference.State.SUBSCRIBED, person=person)

    status = preference_status(account, birthday_event_type, person=person, channel="email")

    assert status.subscribed is True
    assert status.reason == "specific_subscribed"


def test_specific_subscribe_overrides_default_state_muted(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = _member(family)
    event_type = EventType.objects.create(
        family=family,
        code="opt-in-thing",
        name="Opt In Thing",
        default_state=NotificationPreference.State.MUTED,
    )
    _preference(account, event_type, NotificationPreference.State.SUBSCRIBED, person=person)

    assert preference_status(account, event_type, person=person, channel="email").subscribed is True


def test_direct_family_only_includes_ancestor_and_excludes_others(family, yahrzeit_event_type):
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    account = _member(family)
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])

    unrelated = Person.objects.create(family=family, first_name_en="Unrelated", last_name_en="Person")

    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", father=grandparent
    )
    viewer_person.father = parent
    viewer_person.save(update_fields=["father"])

    _preference(account, yahrzeit_event_type, NotificationPreference.State.DIRECT_FAMILY_ONLY)

    grandparent_status = preference_status(account, yahrzeit_event_type, person=grandparent, channel="email")
    unrelated_status = preference_status(account, yahrzeit_event_type, person=unrelated, channel="email")

    assert grandparent_status.subscribed is True
    assert grandparent_status.reason == "type_direct_family_only"
    assert unrelated_status.subscribed is False
    assert unrelated_status.reason == "type_direct_family_only"


def test_direct_family_only_can_still_be_overridden_per_person(family, yahrzeit_event_type):
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    account = _member(family)
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])

    unrelated = Person.objects.create(family=family, first_name_en="Unrelated", last_name_en="Person")
    _preference(account, yahrzeit_event_type, NotificationPreference.State.DIRECT_FAMILY_ONLY)
    _preference(account, yahrzeit_event_type, NotificationPreference.State.SUBSCRIBED, person=unrelated)

    status = preference_status(account, yahrzeit_event_type, person=unrelated, channel="email")

    assert status.subscribed is True
    assert status.reason == "specific_subscribed"


def test_yahrzeit_defaults_to_direct_family_only_with_nothing_set(family, yahrzeit_event_type):
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    account = _member(family)
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])

    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    viewer_person.father = parent
    viewer_person.save(update_fields=["father"])
    unrelated = Person.objects.create(family=family, first_name_en="Unrelated", last_name_en="Person")

    parent_status = preference_status(account, yahrzeit_event_type, person=parent, channel="email")
    unrelated_status = preference_status(account, yahrzeit_event_type, person=unrelated, channel="email")

    assert parent_status.subscribed is True
    assert parent_status.reason == "default"
    assert unrelated_status.subscribed is False
    assert unrelated_status.reason == "default"


def test_immediate_family_only_includes_immediate_and_excludes_others(family):
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    account = _member(family)
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])

    cousin = Person.objects.create(family=family, first_name_en="Cousin", last_name_en="Person")

    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    viewer_person.father = parent
    viewer_person.save(update_fields=["father"])
    sibling = Person.objects.create(
        family=family, first_name_en="Sibling", last_name_en="Person", father=parent
    )

    birthday_event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    _preference(account, birthday_event_type, NotificationPreference.State.IMMEDIATE_FAMILY_ONLY)

    sibling_status = preference_status(account, birthday_event_type, person=sibling, channel="email")
    cousin_status = preference_status(account, birthday_event_type, person=cousin, channel="email")

    assert sibling_status.subscribed is True
    assert sibling_status.reason == "type_immediate_only"
    assert cousin_status.subscribed is False
    assert cousin_status.reason == "type_immediate_only"


def test_immediate_family_only_can_still_be_overridden_per_person(family):
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    account = _member(family)
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])

    cousin = Person.objects.create(family=family, first_name_en="Cousin", last_name_en="Person")
    birthday_event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    _preference(account, birthday_event_type, NotificationPreference.State.IMMEDIATE_FAMILY_ONLY)
    _preference(account, birthday_event_type, NotificationPreference.State.SUBSCRIBED, person=cousin)

    status = preference_status(account, birthday_event_type, person=cousin, channel="email")

    assert status.subscribed is True
    assert status.reason == "specific_subscribed"


def test_immediate_family_only_fails_closed_without_a_viewer_person(family, birthday_event_type):
    # The account has no Person record in this family at all - can't place
    # them in the tree, so "immediate family only" can't include anyone.
    account = _member(family)
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _preference(account, birthday_event_type, NotificationPreference.State.IMMEDIATE_FAMILY_ONLY)

    status = preference_status(account, birthday_event_type, person=person, channel="email")

    assert status.subscribed is False


def _editor(family, email="editor@example.com"):
    account = Account.objects.create_user(email=email)
    FamilyMembership.objects.create(account=account, family=family, role=FamilyMembership.Role.EDITOR)
    return account


def test_visibility_nobody_excludes_a_default_subscribed_member(family, birthday_event_type):
    person = Person.objects.create(
        family=family, first_name_en="Private", last_name_en="Person", visibility=Person.Visibility.NOBODY
    )
    account = _member(family)

    status = preference_status(account, birthday_event_type, person=person, channel="email")

    assert status.subscribed is False
    assert status.reason == "not_visible"


def test_visibility_nobody_still_includes_an_editor(family, birthday_event_type):
    person = Person.objects.create(
        family=family, first_name_en="Private", last_name_en="Person", visibility=Person.Visibility.NOBODY
    )
    editor = _editor(family)

    assert preference_status(editor, birthday_event_type, person=person, channel="email").subscribed is True


def test_visibility_immediate_family_includes_a_parent_and_excludes_a_stranger(family, birthday_event_type):
    parent_account = _member(family, email="parent@example.com")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", account=parent_account
    )
    person = Person.objects.create(
        family=family,
        first_name_en="Kid",
        last_name_en="Person",
        father=parent,
        visibility=Person.Visibility.IMMEDIATE_FAMILY,
    )
    stranger = _member(family, email="stranger@example.com")

    assert (
        preference_status(parent_account, birthday_event_type, person=person, channel="email").subscribed
        is True
    )
    assert (
        preference_status(stranger, birthday_event_type, person=person, channel="email").subscribed is False
    )


def test_visibility_cannot_be_overridden_by_a_specific_subscribe(family, birthday_event_type):
    """The whole point of a ceiling: an explicit per-account "subscribed"
    override for this person must not see past their own visibility."""
    person = Person.objects.create(
        family=family, first_name_en="Private", last_name_en="Person", visibility=Person.Visibility.NOBODY
    )
    account = _member(family)
    _preference(account, birthday_event_type, NotificationPreference.State.SUBSCRIBED, person=person)

    status = preference_status(account, birthday_event_type, person=person, channel="email")

    assert status.subscribed is False
    assert status.reason == "not_visible"


def test_visibility_for_a_union_is_satisfied_by_either_spouse(family):
    # Anniversary defaults to opt-in (MUTED), unrelated to visibility - an
    # explicit whole-type subscribe isolates the visibility check itself.
    anniversary = EventType.objects.get(family=None, code=EventType.BuiltinCode.ANNIVERSARY)
    visible_spouse = Person.objects.create(family=family, first_name_en="A", last_name_en="Person")
    private_spouse = Person.objects.create(
        family=family, first_name_en="B", last_name_en="Person", visibility=Person.Visibility.NOBODY
    )
    union = Union.objects.create(
        person_a=visible_spouse, person_b=private_spouse, status=Union.Status.MARRIED
    )
    account = _member(family)
    _preference(account, anniversary, NotificationPreference.State.SUBSCRIBED)

    status = preference_status(account, anniversary, union=union, channel="email")

    assert status.subscribed is True


@pytest.fixture
def broadcast_event_type():
    return EventType.objects.get(family=None, code=EventType.BuiltinCode.BROADCAST)


def test_broadcast_audience_is_everyone_in_the_family_by_default_with_no_tied_people(
    family, broadcast_event_type
):
    account = _member(family)

    audience = resolve_broadcast_audience(event_type=broadcast_event_type, family=family, people=[])

    assert (account, "email", account.email) in audience


def test_broadcast_audience_excludes_accounts_outside_the_family(family, broadcast_event_type):
    other_family = Family.objects.create(name="Other Family")
    _member(other_family, email="outsider@example.com")

    audience = resolve_broadcast_audience(event_type=broadcast_event_type, family=family, people=[])

    assert audience == []


def test_broadcast_audience_whole_type_mute_excludes_the_account(family, broadcast_event_type):
    account = _member(family)
    _preference(account, broadcast_event_type, NotificationPreference.State.MUTED)

    audience = resolve_broadcast_audience(event_type=broadcast_event_type, family=family, people=[])

    assert audience == []


def test_broadcast_audience_immediate_family_only_includes_an_account_related_to_any_tied_person(
    family, broadcast_event_type
):
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    account = _member(family)
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])
    _preference(account, broadcast_event_type, NotificationPreference.State.IMMEDIATE_FAMILY_ONLY)

    sibling = Person.objects.create(family=family, first_name_en="Sib", last_name_en="Person")
    cousin = Person.objects.create(family=family, first_name_en="Cousin", last_name_en="Person")
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    viewer_person.father = parent
    viewer_person.save(update_fields=["father"])
    sibling.father = parent
    sibling.save(update_fields=["father"])

    # Tied to a cousin (not immediate family) and a sibling (is) -
    # included because of the sibling, even though the cousin alone
    # wouldn't qualify.
    audience = resolve_broadcast_audience(
        event_type=broadcast_event_type, family=family, people=[cousin, sibling]
    )

    assert (account, "email", account.email) in audience


def test_broadcast_audience_immediate_family_only_excludes_when_no_tied_person_qualifies(
    family, broadcast_event_type
):
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    account = _member(family)
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])
    _preference(account, broadcast_event_type, NotificationPreference.State.IMMEDIATE_FAMILY_ONLY)

    cousin = Person.objects.create(family=family, first_name_en="Cousin", last_name_en="Person")

    audience = resolve_broadcast_audience(event_type=broadcast_event_type, family=family, people=[cousin])

    assert audience == []


def test_broadcast_audience_whole_type_immediate_family_only_excludes_with_no_tied_people(
    family, broadcast_event_type
):
    # No tied people means there's no subject to be "immediate family of"
    # - this used to crash with AttributeError (union.person_a on a None
    # union), since the docstring's claim that this state is unreachable
    # for Broadcast was wrong: NotificationPreference.clean() only blocks
    # a person/union-scoped Broadcast row, not this whole-type state.
    # The account must resolve to a real viewer Person (not just any
    # family member) to actually reach that code path - an account with
    # no linked Person short-circuits earlier via the unrelated
    # viewer-is-None fail-closed check.
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    account = _member(family)
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])
    _preference(account, broadcast_event_type, NotificationPreference.State.IMMEDIATE_FAMILY_ONLY)

    audience = resolve_broadcast_audience(event_type=broadcast_event_type, family=family, people=[])

    assert audience == []


def test_broadcast_audience_deduplicates_across_multiple_tied_people(family, broadcast_event_type):
    account = _member(family)
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person")

    audience = resolve_broadcast_audience(
        event_type=broadcast_event_type, family=family, people=[person_a, person_b]
    )

    assert audience.count((account, "email", account.email)) == 1


def test_preference_resolver_spouse_pairs_reaches_a_cross_family_marriage(two_families):
    # A Union can legitimately span two Family tenants (the one deliberate
    # crack in the tenant boundary - see family/access.py's own docstring
    # and tenants/AGENTS.md's "in-law reachable through a marriage" rule).
    # PreferenceResolver's own _spouse_pairs used to require BOTH sides of
    # a marriage to fall inside its caller-supplied family_ids scope,
    # silently treating a person as spouseless whenever their own family
    # was in scope but their spouse's wasn't - even though "are these two
    # people married" doesn't depend on which family_ids a particular
    # caller happened to scope the resolver to.
    family_a, family_b, _account_a, _account_b = two_families
    person_in_a = Person.objects.create(family=family_a, first_name_en="A", last_name_en="Person")
    spouse_in_b = Person.objects.create(family=family_b, first_name_en="B", last_name_en="Person")
    Union.objects.create(person_a=person_in_a, person_b=spouse_in_b, status=Union.Status.MARRIED)

    # Scoped to family_a only - the same shape resolve_audience uses for a
    # person-anchored event, where family_ids never includes a spouse's
    # own, separate family.
    resolver = PreferenceResolver(account_ids=[], family_ids=[family_a.id])

    assert resolver._cached_spouse_check(person_in_a, spouse_in_b) is True
    assert spouse_in_b in resolver._cached_spouses_of(person_in_a)


def test_resolve_audience_query_count_does_not_scale_with_account_count(family, birthday_event_type):
    # PreferenceResolver (see notifications.audience) batches preference and
    # immediate-family lookups into a fixed number of queries regardless of
    # how many accounts are in the family - before that, resolve_audience
    # ran 2-6 queries per account. Proven here by asserting the same query
    # count for a small and a much larger family, not just an arbitrary cap.
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _member(family, email="one@example.com")
    _member(family, email="two@example.com")

    with CaptureQueriesContext(connection) as small:
        resolve_audience(event_type=birthday_event_type, person=person)

    for i in range(10):
        _member(family, email=f"extra{i}@example.com")

    with CaptureQueriesContext(connection) as large:
        resolve_audience(event_type=birthday_event_type, person=person)

    assert len(large.captured_queries) == len(small.captured_queries)
