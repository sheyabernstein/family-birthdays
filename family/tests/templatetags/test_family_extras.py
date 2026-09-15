import datetime as dt

import pytest

from family.models import Person
from family.templatetags.family_extras import hebrew_str, with_hebrew_first_name

pytestmark = pytest.mark.django_db


def test_hebrew_str_omits_the_thousands_digit():
    assert hebrew_str(dt.date(2026, 3, 4)) == 'ט"ו אדר תשפ"ו'


def test_hebrew_str_handles_none():
    assert hebrew_str(None) == ""


def test_with_hebrew_first_name_renders_a_trailing_parenthetical(family):
    person = Person.objects.create(
        family=family, first_name_en="Blimi", last_name_en="Rokach", first_name_he="בלומא"
    )
    assert with_hebrew_first_name(person) == " (בלומא)"


def test_with_hebrew_first_name_is_empty_without_one(family):
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")
    assert with_hebrew_first_name(person) == ""


def test_with_hebrew_first_name_handles_none():
    assert with_hebrew_first_name(None) == ""
