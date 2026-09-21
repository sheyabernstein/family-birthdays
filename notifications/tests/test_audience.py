import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from family.models import Person, Union
from notifications.audience import (
    channels_for_account,
    is_ancestor,
    is_immediate_family,
    preference_status,
    resolve_audience,
    resolve_broadcast_audience,
)
from notifications.models import EventType, NotificationPreference
from notifications.tests.conftest import member as _member
from tenants.models import Family

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


def test_parent_and_child_are_immediate_family(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    child = Person.objects.create(family=family, first_name_en="Child", last_name_en="Person", father=parent)

    assert is_immediate_family(parent, child) is True
    assert is_immediate_family(child, parent) is True


def test_siblings_sharing_one_parent_are_immediate_family(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person", father=parent)
    b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person", father=parent)

    assert is_immediate_family(a, b) is True


@pytest.mark.parametrize(
    ["status", "expected"],
    [
        [Union.Status.MARRIED, True],
        [Union.Status.DIVORCED, False],
    ],
    ids=[
        "married spouses are immediate family",
        "unmarried partners are not immediate family",
    ],
)
def test_spouse_status_determines_immediate_family(family, status, expected):
    a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person")
    b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person")
    Union.objects.create(person_a=a, person_b=b, status=status)

    assert is_immediate_family(a, b) is expected


def test_grandparent_is_not_immediate_family(family):
    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", father=grandparent
    )
    grandchild = Person.objects.create(
        family=family, first_name_en="Child", last_name_en="Person", father=parent
    )

    assert is_immediate_family(grandparent, grandchild) is False


def test_cousin_is_not_immediate_family(family):
    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent_a = Person.objects.create(
        family=family, first_name_en="ParentA", last_name_en="Person", father=grandparent
    )
    parent_b = Person.objects.create(
        family=family, first_name_en="ParentB", last_name_en="Person", father=grandparent
    )
    cousin_a = Person.objects.create(
        family=family, first_name_en="CousinA", last_name_en="Person", father=parent_a
    )
    cousin_b = Person.objects.create(
        family=family, first_name_en="CousinB", last_name_en="Person", father=parent_b
    )

    assert is_immediate_family(cousin_a, cousin_b) is False


def test_parent_is_an_ancestor(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    child = Person.objects.create(family=family, first_name_en="Child", last_name_en="Person", father=parent)

    assert is_ancestor(child, parent) is True
    assert is_ancestor(parent, child) is False


def test_grandparent_via_mother_line_is_an_ancestor(family):
    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", mother=grandparent
    )
    grandchild = Person.objects.create(
        family=family, first_name_en="Child", last_name_en="Person", mother=parent
    )

    assert is_ancestor(grandchild, grandparent) is True


def test_sibling_is_not_an_ancestor(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person", father=parent)
    b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person", father=parent)

    assert is_ancestor(a, b) is False


def test_person_is_not_their_own_ancestor(family):
    person = Person.objects.create(family=family, first_name_en="Solo", last_name_en="Person")

    assert is_ancestor(person, person) is False


def test_ancestors_only_includes_ancestor_and_excludes_others(family, yahrzeit_event_type):
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

    _preference(account, yahrzeit_event_type, NotificationPreference.State.ANCESTORS_ONLY)

    grandparent_status = preference_status(account, yahrzeit_event_type, person=grandparent, channel="email")
    unrelated_status = preference_status(account, yahrzeit_event_type, person=unrelated, channel="email")

    assert grandparent_status.subscribed is True
    assert grandparent_status.reason == "type_ancestors_only"
    assert unrelated_status.subscribed is False
    assert unrelated_status.reason == "type_ancestors_only"


def test_ancestors_only_can_still_be_overridden_per_person(family, yahrzeit_event_type):
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    account = _member(family)
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])

    unrelated = Person.objects.create(family=family, first_name_en="Unrelated", last_name_en="Person")
    _preference(account, yahrzeit_event_type, NotificationPreference.State.ANCESTORS_ONLY)
    _preference(account, yahrzeit_event_type, NotificationPreference.State.SUBSCRIBED, person=unrelated)

    status = preference_status(account, yahrzeit_event_type, person=unrelated, channel="email")

    assert status.subscribed is True
    assert status.reason == "specific_subscribed"


def test_yahrzeit_defaults_to_ancestors_only_with_nothing_set(family, yahrzeit_event_type):
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


def test_broadcast_audience_deduplicates_across_multiple_tied_people(family, broadcast_event_type):
    account = _member(family)
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person")

    audience = resolve_broadcast_audience(
        event_type=broadcast_event_type, family=family, people=[person_a, person_b]
    )

    assert audience.count((account, "email", account.email)) == 1


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
