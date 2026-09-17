import datetime as dt

import pytest

from family.models import Person, Union
from notifications.models import EventType, Occurrence

pytestmark = pytest.mark.django_db


def _raise(*args, **kwargs):
    raise RuntimeError("boom")


def test_saving_a_person_computes_their_own_occurrences(family):
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_hebrew_year=5751,
        dob_hebrew_month=1,
        dob_hebrew_day=3,
    )

    assert Occurrence.objects.filter(person=person, event_type__code=EventType.BuiltinCode.BIRTHDAY).exists()


def test_saving_a_person_also_recomputes_their_unions(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_hebrew_year=5780,
        marriage_hebrew_month=1,
        marriage_hebrew_day=1,
    )
    assert Occurrence.objects.filter(union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY).exists()

    person_a.dod_gregorian = dt.date(2020, 1, 1)
    person_a.save(update_fields=["dod_gregorian", "is_living"])

    assert not Occurrence.objects.filter(
        union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY, is_sent=False
    ).exists()


def test_saving_a_union_computes_its_own_occurrences(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")

    union = Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_hebrew_year=5780,
        marriage_hebrew_month=1,
        marriage_hebrew_day=1,
    )

    assert Occurrence.objects.filter(union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY).exists()


def test_person_recompute_failure_is_logged_and_does_not_raise(monkeypatch, family):
    monkeypatch.setattr("family.signals.compute_occurrences_for_person", _raise)

    # Doesn't raise - form_valid()/the admin's save should still succeed
    # even if the recompute itself blows up.
    Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")


def test_union_recompute_failure_is_logged_and_does_not_raise(monkeypatch, family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    monkeypatch.setattr("family.signals.compute_occurrences_for_union", _raise)

    Union.objects.create(person_a=person_a, person_b=person_b)
