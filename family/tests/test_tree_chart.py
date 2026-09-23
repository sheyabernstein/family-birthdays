import datetime as dt

import pytest
from django.utils import timezone

from family.models import Person, Union
from family.tree_chart import build_chart_data

pytestmark = pytest.mark.django_db


def test_build_chart_data_falls_back_to_the_hebrew_name_with_only_a_last_name_en(family):
    # first_name_en checked alone, not "first_name_en or last_name_en" -
    # a person with only a last_name_en on file (first_name_en/
    # last_name_en are both optional; first_name_he is the one required
    # name) used to show a bare surname card ("Rokach") instead of
    # falling back to their Hebrew name, matching a bug Person.
    # display_name itself had before it was fixed.
    person = Person.objects.create(family=family, last_name_en="Rokach", first_name_he="בלומא")

    nodes = build_chart_data(Person.objects.filter(family=family), main_person=person)

    data = nodes[0]["data"]
    assert data["first name"] == "בלומא"
    assert data["last name"] == ""


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


def test_build_chart_data_flags_name_is_hebrew_for_the_no_english_name_fallback(family):
    # Drives the card's own RTL wrapping for the deceased marker (see
    # family_tree.html's setCardInnerHtmlCreator) - derived from the
    # same first_name_en/last_name_en check _display_name() itself uses
    # to build "first name"/"last name", not Person.display_name_is_
    # hebrew (which also considers a Hebrew nickname - _display_name()
    # ignores nickname entirely, a separate, pre-existing inconsistency
    # this doesn't paper over).
    english = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")
    hebrew_only = Person.objects.create(family=family, last_name_en="Rokach", first_name_he="בלומא")

    nodes = build_chart_data(Person.objects.filter(family=family), main_person=english)

    by_id = {n["id"]: n for n in nodes}
    assert by_id[str(english.uuid)]["data"]["name_is_hebrew"] is False
    assert by_id[str(hebrew_only.uuid)]["data"]["name_is_hebrew"] is True


def test_build_chart_data_omits_the_hebrew_name_subtitle_for_the_no_english_name_fallback(family):
    # Otherwise the Hebrew name would show twice on the same card - once
    # as the primary name (the fallback), once again as the secondary
    # subtitle line.
    hebrew_only = Person.objects.create(family=family, last_name_en="Rokach", first_name_he="בלומא")

    nodes = build_chart_data(Person.objects.filter(family=family), main_person=hebrew_only)

    by_id = {n["id"]: n for n in nodes}
    assert by_id[str(hebrew_only.uuid)]["data"]["hebrew_name"] == ""
