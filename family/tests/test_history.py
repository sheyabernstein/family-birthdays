import datetime as dt

import pytest
import reversion
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Account
from family.history import HISTORY_LIMIT, person_history
from family.models import Person, Union

pytestmark = pytest.mark.django_db


def test_person_history_first_version_is_a_created_event(family):
    with reversion.create_revision():
        person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")

    events = person_history(person, unions=[])

    assert len(events) == 1
    assert events[0].changes[0].label == "Record created"
    assert events[0].changes[0].old_display is None
    assert events[0].changes[0].new_display is None
    assert events[0].subject_label == "This person"


def test_person_history_labels_a_pre_existing_records_first_version_distinctly(family):
    # A record that existed before reversion started tracking it (e.g.
    # bulk-imported outside any request) shouldn't claim to have been
    # "created" the moment someone happened to first save it afterwards.
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    Person.objects.filter(pk=person.pk).update(created_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
    person.refresh_from_db()

    with reversion.create_revision():
        person.notes = "touched for the first tracked save"
        person.save()

    events = person_history(person, unions=[])

    assert events[0].changes[0].label == "Earliest recorded version"


def test_person_history_diffs_a_field_change(family):
    account = Account.objects.create_user(email="editor@example.com")
    with reversion.create_revision():
        person = Person.objects.create(family=family, first_name_en="Old", last_name_en="Name")

    with reversion.create_revision():
        reversion.set_user(account)
        person.first_name_en = "New"
        person.save()

    events = person_history(person, unions=[])

    assert len(events) == 2
    latest = events[0]
    assert latest.who == account
    assert any(change.old_display == "Old" and change.new_display == "New" for change in latest.changes)


def test_person_history_ignores_a_save_with_no_actual_field_change(family):
    with reversion.create_revision():
        person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")

    with reversion.create_revision():
        person.save()

    events = person_history(person, unions=[])

    assert len(events) == 1


def test_person_history_resolves_a_foreign_key_to_its_display_name(family):
    with reversion.create_revision():
        father = Person.objects.create(
            family=family, first_name_en="Dad", last_name_en="Test", gender=Person.Gender.MALE
        )
    with reversion.create_revision():
        child = Person.objects.create(family=family, first_name_en="Kid", last_name_en="Test")

    with reversion.create_revision():
        child.father = father
        child.save()

    events = person_history(child, unions=[])

    latest = events[0]
    assert any(change.new_display == "Dad Test" for change in latest.changes)


def test_person_history_includes_union_events_with_a_partner_label(family):
    with reversion.create_revision():
        person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    with reversion.create_revision():
        person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    with reversion.create_revision():
        union = Union.objects.create(person_a=person_a, person_b=person_b)

    events = person_history(person_a, unions=[union])

    union_events = [e for e in events if e.subject_label == "Marriage to B Test"]
    assert len(union_events) == 1
    assert union_events[0].changes[0].label == "Record created"


def test_person_history_caps_to_the_history_limit(family):
    with reversion.create_revision():
        person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")

    for i in range(HISTORY_LIMIT + 5):
        with reversion.create_revision():
            person.notes = f"note {i}"
            person.save()

    events = person_history(person, unions=[])

    assert len(events) == HISTORY_LIMIT


def test_person_history_shows_a_placeholder_for_a_blank_string_not_just_null(family):
    # notes/nickname are blank=True, not null=True - they store "" rather
    # than None, so the diff placeholder needs to cover both.
    with reversion.create_revision():
        person = Person.objects.create(
            family=family, first_name_en="Test", last_name_en="Person", notes="old note"
        )

    with reversion.create_revision():
        person.notes = ""
        person.save()

    events = person_history(person, unions=[])

    latest = events[0]
    notes_change = next(change for change in latest.changes if change.label == "notes")
    assert notes_change.new_display == ""


def test_person_history_hides_bookkeeping_fields(family):
    with reversion.create_revision():
        person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")

    with reversion.create_revision():
        person.first_name_en = "Changed"
        person.save()

    events = person_history(person, unions=[])

    latest = events[0]
    assert not any(change.label == "updated_at" for change in latest.changes)
    assert not any(change.label == "id" for change in latest.changes)


def test_person_history_query_count_does_not_scale_with_changed_fk_count(family):
    # _field_display used to resolve every changed father/mother FK with
    # its own query, per version - see family.history's batching and
    # AGENTS.md's note on this. Proven by asserting the same query count
    # for a person with few father changes and one with many more.
    with reversion.create_revision():
        person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")

    def _add_father_changes(count):
        for i in range(count):
            with reversion.create_revision():
                person.father = Person.objects.create(
                    family=family, first_name_en=f"Dad{i}", last_name_en="Test"
                )
                person.save()

    _add_father_changes(2)
    with CaptureQueriesContext(connection) as few:
        person_history(person, unions=[])

    _add_father_changes(10)
    with CaptureQueriesContext(connection) as many:
        person_history(person, unions=[])

    assert len(many.captured_queries) == len(few.captured_queries)
