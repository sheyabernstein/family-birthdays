import datetime as dt

import pytest
from django.utils import timezone

from family.models import Person, Union
from family.tree_chart import build_chart_data

pytestmark = pytest.mark.django_db


def test_build_chart_data_flags_the_upcoming_wedding_partner(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_date_gregorian=timezone.localdate() + dt.timedelta(days=30),
    )

    nodes = build_chart_data(Person.objects.filter(family=family), main_person=person_a)

    by_id = {n["id"]: n for n in nodes}
    assert by_id[str(person_a.uuid)]["data"]["upcoming_wedding_partner"] == "B Test"
    assert by_id[str(person_b.uuid)]["data"]["upcoming_wedding_partner"] == "A Test"
    # The uuid, not just the display text, is what lets the tree JS pick
    # out this exact spouse link to dash (see markUpcomingWeddingLinks in
    # family_tree.html) - a text match alone couldn't do that.
    assert by_id[str(person_a.uuid)]["data"]["upcoming_wedding_partner_id"] == str(person_b.uuid)
    assert by_id[str(person_b.uuid)]["data"]["upcoming_wedding_partner_id"] == str(person_a.uuid)


def test_build_chart_data_does_not_flag_an_already_married_union(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_date_gregorian=timezone.localdate() - dt.timedelta(days=30),
    )

    nodes = build_chart_data(Person.objects.filter(family=family), main_person=person_a)

    by_id = {n["id"]: n for n in nodes}
    assert by_id[str(person_a.uuid)]["data"]["upcoming_wedding_partner"] == ""
    assert by_id[str(person_a.uuid)]["data"]["upcoming_wedding_partner_id"] == ""


def test_build_chart_data_birth_sort_is_an_opaque_rank_not_the_real_date(family):
    # Never the real ordinal - see _birth_rank_by_id's own docstring for
    # why (a raw date.toordinal() is trivially reversible back to an
    # exact birthdate by anyone reading the chart's own JSON payload).
    older = Person.objects.create(
        family=family, first_name_en="Older", last_name_en="Test", dob_gregorian=dt.date(1980, 1, 1)
    )
    younger = Person.objects.create(
        family=family, first_name_en="Younger", last_name_en="Test", dob_gregorian=dt.date(2010, 1, 1)
    )
    unknown = Person.objects.create(family=family, first_name_en="Unknown", last_name_en="Test")

    nodes = build_chart_data(Person.objects.filter(family=family), main_person=older)

    by_id = {n["id"]: n for n in nodes}
    older_rank = by_id[str(older.uuid)]["data"]["birth_sort"]
    younger_rank = by_id[str(younger.uuid)]["data"]["birth_sort"]
    assert older_rank < younger_rank
    assert older_rank != dt.date(1980, 1, 1).toordinal()
    assert by_id[str(unknown.uuid)]["data"]["birth_sort"] is None
