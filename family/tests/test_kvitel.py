import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from family.kvitel import build_kvitel
from family.models import Person, Union
from tenants.models import Family

pytestmark = pytest.mark.django_db


def _person(family, **kwargs):
    kwargs.setdefault("first_name_he", "פלוני")
    return Person.objects.create(family=family, **kwargs)


def _marry(person_a, person_b, **kwargs):
    kwargs.setdefault("status", Union.Status.MARRIED)
    return Union.objects.create(person_a=person_a, person_b=person_b, **kwargs)


def test_build_kvitel_lists_husband_then_wife(family):
    husband = _person(family, first_name_en="H", first_name_he="בעל", gender=Person.Gender.MALE)
    wife = _person(family, first_name_en="W", first_name_he="אישה", gender=Person.Gender.FEMALE)
    _marry(husband, wife)

    lines = build_kvitel(husband, viewer=None, can_edit=True)

    assert [line.role for line in lines] == ["husband", "wife"]
    assert lines[0].text == "בעל"
    assert lines[1].text == "ז' אישה"


def test_build_kvitel_husband_lists_first_regardless_of_which_spouse_is_the_root(family):
    husband = _person(family, first_name_he="בעל", gender=Person.Gender.MALE)
    wife = _person(family, first_name_he="אישה", gender=Person.Gender.FEMALE)
    _marry(husband, wife)

    lines = build_kvitel(wife, viewer=None, can_edit=True)

    assert [line.role for line in lines] == ["husband", "wife"]


def test_build_kvitel_husband_lists_first_even_with_his_own_gender_unrecorded(family):
    # Found for real against live data: a wife correctly marked female,
    # married to a husband with no gender on file at all - requiring
    # both sides' gender to be explicitly known left her listed first,
    # unprefixed, as if she were the husband.
    husband = _person(family, first_name_he="בעל")  # gender left blank
    wife = _person(family, first_name_he="אישה", gender=Person.Gender.FEMALE)
    _marry(husband, wife)

    lines = build_kvitel(wife, viewer=None, can_edit=True)

    assert [line.role for line in lines] == ["husband", "wife"]
    assert lines[0].text == "בעל"
    assert lines[1].text == "ז' אישה"


def test_build_kvitel_wife_lists_second_even_with_her_own_gender_unrecorded(family):
    husband = _person(family, first_name_he="בעל", gender=Person.Gender.MALE)
    wife = _person(family, first_name_he="אישה")  # gender left blank
    _marry(husband, wife)

    lines = build_kvitel(wife, viewer=None, can_edit=True)

    assert [line.role for line in lines] == ["husband", "wife"]
    assert lines[0].text == "בעל"


def test_build_kvitel_omits_the_spousal_line_when_there_is_no_spouse(family):
    root = _person(family, first_name_he="לבד")

    lines = build_kvitel(root, viewer=None, can_edit=True)

    assert [line.role for line in lines] == ["husband"]


def test_build_kvitel_omits_a_deceased_spouse(family):
    root = _person(family, first_name_he="בעל", gender=Person.Gender.MALE)
    deceased_spouse = _person(
        family, first_name_he="נפטרת", gender=Person.Gender.FEMALE, dod_gregorian=dt.date(2020, 1, 1)
    )
    _marry(root, deceased_spouse)

    lines = build_kvitel(root, viewer=None, can_edit=True)

    assert [line.role for line in lines] == ["husband"]


def test_build_kvitel_lists_children_bare_by_first_name_oldest_to_youngest(family):
    husband = _person(family, first_name_he="בעל", gender=Person.Gender.MALE)
    wife = _person(family, first_name_he="אישה", gender=Person.Gender.FEMALE)
    _marry(husband, wife)
    _person(family, first_name_he="צעיר", father=husband, mother=wife, dob_gregorian=dt.date(2010, 1, 1))
    _person(family, first_name_he="בכור", father=husband, mother=wife, dob_gregorian=dt.date(2000, 1, 1))
    _person(family, first_name_he="בלי תאריך", father=husband, mother=wife)

    lines = build_kvitel(husband, viewer=None, can_edit=True)

    child_lines = [line.text for line in lines if line.role == "child"]
    assert child_lines == ["ב' בכור", "ב' צעיר", "ב' בלי תאריך"]


def test_build_kvitel_marks_the_last_line_of_a_block_as_ending_it(family):
    husband = _person(family, first_name_he="בעל", gender=Person.Gender.MALE)
    wife = _person(family, first_name_he="אישה", gender=Person.Gender.FEMALE)
    _marry(husband, wife)
    _person(family, first_name_he="ילד", father=husband, mother=wife)

    lines = build_kvitel(husband, viewer=None, can_edit=True)

    assert [line.ends_block for line in lines] == [False, False, True]


def test_build_kvitel_recurses_into_a_married_childs_own_block(family):
    husband = _person(family, first_name_he="בעל", gender=Person.Gender.MALE)
    wife = _person(family, first_name_he="אישה", gender=Person.Gender.FEMALE)
    _marry(husband, wife)
    son = _person(family, first_name_he="בן", father=husband, mother=wife, gender=Person.Gender.MALE)
    daughter_in_law = _person(family, first_name_he="כלה", gender=Person.Gender.FEMALE)
    _marry(son, daughter_in_law)

    lines = build_kvitel(husband, viewer=None, can_edit=True)

    # A complex child becomes the subject of their own nested block, so
    # - unlike a simple child's bare first name - they get the full
    # matronymic label too, matching the real sheet (e.g. "יהושע העשיל
    # בן בלומא").
    assert [line.role for line in lines] == ["husband", "wife", "husband", "wife"]
    assert lines[2].text == "בן בן אישה"
    assert lines[3].text == "ז' כלה"


def test_build_kvitel_does_not_recurse_into_a_childless_unmarried_child(family):
    husband = _person(family, first_name_he="בעל", gender=Person.Gender.MALE)
    wife = _person(family, first_name_he="אישה", gender=Person.Gender.FEMALE)
    _marry(husband, wife)
    _person(family, first_name_he="ילד", father=husband, mother=wife)

    lines = build_kvitel(husband, viewer=None, can_edit=True)

    assert len(lines) == 3
    assert lines[2].role == "child"


def test_build_kvitel_still_recurses_past_a_fully_deceased_couple(family):
    husband = _person(
        family, first_name_he="בעל", gender=Person.Gender.MALE, dod_gregorian=dt.date(2020, 1, 1)
    )
    wife = _person(
        family, first_name_he="אישה", gender=Person.Gender.FEMALE, dod_gregorian=dt.date(2021, 1, 1)
    )
    _marry(husband, wife)
    grandson = _person(family, first_name_he="נכד", father=husband, mother=wife, gender=Person.Gender.MALE)
    grandson_wife = _person(family, first_name_he="כלה", gender=Person.Gender.FEMALE)
    _marry(grandson, grandson_wife)

    lines = build_kvitel(husband, viewer=None, can_edit=True)

    # No lines at all for the deceased couple themselves, but their
    # grandson's own block still renders (with his own matronymic label,
    # same reasoning as the test above).
    assert [line.text for line in lines] == ["נכד בן אישה", "ז' כלה"]


def test_build_kvitel_falls_back_to_a_bare_first_name_with_no_mother_recorded(family):
    root = _person(family, first_name_he="בעל", gender=Person.Gender.MALE)

    lines = build_kvitel(root, viewer=None, can_edit=True)

    assert lines[0].text == "בעל"


def test_build_kvitel_uses_the_mothers_name_not_the_fathers(family):
    father = _person(family, first_name_he="אבא", gender=Person.Gender.MALE)
    mother = _person(family, first_name_he="אמא", gender=Person.Gender.FEMALE)
    child = _person(family, first_name_he="ילד", father=father, mother=mother, gender=Person.Gender.MALE)
    spouse = _person(family, first_name_he="בת זוג", gender=Person.Gender.FEMALE)
    _marry(child, spouse)

    lines = build_kvitel(child, viewer=None, can_edit=True)

    assert lines[0].text == "ילד בן אמא"


def test_build_kvitel_is_safe_against_a_cross_cousin_marriage_cycle(family):
    grandparent_a = _person(family, first_name_he="סבא", gender=Person.Gender.MALE)
    grandparent_b = _person(family, first_name_he="סבתא", gender=Person.Gender.FEMALE)
    _marry(grandparent_a, grandparent_b)
    child_a = _person(
        family, first_name_he="בן", father=grandparent_a, mother=grandparent_b, gender=Person.Gender.MALE
    )
    child_b = _person(
        family, first_name_he="בת", father=grandparent_a, mother=grandparent_b, gender=Person.Gender.FEMALE
    )
    grandchild = _person(
        family, first_name_he="נכד", father=child_a, mother=child_b, gender=Person.Gender.MALE
    )
    _marry(child_a, child_b)
    _marry(grandchild, _person(family, first_name_he="כלה", gender=Person.Gender.FEMALE))

    lines = build_kvitel(grandparent_a, viewer=None, can_edit=True)

    # Doesn't hang, and the siblings-married-to-each-other couple only
    # gets rendered once - not once per sibling, and not an infinite
    # loop down through their own shared descendant.
    assert sum(1 for line in lines if line.text.startswith("נכד")) == 1
    assert sum(1 for line in lines if line.text.startswith("בן ")) == 1


def test_build_kvitel_ignores_a_spouse_tracked_only_in_a_different_family():
    own_family = Family.objects.create(name="Own Family")
    other_family = Family.objects.create(name="Other Family")
    root = _person(own_family, first_name_he="בעל", gender=Person.Gender.MALE)
    in_law_spouse = _person(other_family, first_name_he="כלה", gender=Person.Gender.FEMALE)
    _marry(root, in_law_spouse)

    lines = build_kvitel(root, viewer=None, can_edit=True)

    # Doesn't crash looking up a spouse who was never fetched (a
    # different family's own person) - just renders as if unmarried.
    assert [line.role for line in lines] == ["husband"]


def test_build_kvitel_query_count_does_not_scale_with_generation_depth(family):
    root = current = _person(family, first_name_he="שורש", gender=Person.Gender.MALE)
    for i in range(10):
        spouse = _person(family, first_name_he=f"זוג {i}", gender=Person.Gender.FEMALE)
        _marry(current, spouse)
        current = _person(
            family, first_name_he=f"דור {i}", father=current, mother=spouse, gender=Person.Gender.MALE
        )

    with CaptureQueriesContext(connection) as captured:
        build_kvitel(root, viewer=None, can_edit=True)

    assert len(captured.captured_queries) == 2
