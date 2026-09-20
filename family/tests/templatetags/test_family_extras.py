import datetime as dt

import pytest
from freezegun import freeze_time

from family.models import Person
from family.templatetags.family_extras import hebrew_str, weekday_naturalday, with_hebrew_first_name

pytestmark = pytest.mark.django_db


def test_hebrew_str_omits_the_thousands_digit():
    assert hebrew_str(dt.date(2026, 3, 4)) == 'ט"ו אדר תשפ"ו'


def test_hebrew_str_handles_none():
    assert hebrew_str(None) == ""


def test_hebrew_str_drops_the_year_entirely_when_asked():
    assert hebrew_str(dt.date(2026, 3, 4), False) == 'ט"ו אדר'


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


@pytest.mark.parametrize(
    ["delta_days", "expected"],
    [
        [-1, "yesterday"],
        [0, "today"],
        [1, "tomorrow"],
        [2, "on Tuesday"],
        [6, "on Saturday"],
        [-6, "on Monday"],
    ],
    ids=[
        "yesterday still uses naturalday's own word",
        "today still uses naturalday's own word",
        "tomorrow still uses naturalday's own word",
        "two days ahead names the weekday",
        "six days ahead names the weekday",
        "six days ago names the weekday",
    ],
)
def test_weekday_naturalday_names_the_weekday_within_a_week(delta_days, expected):
    with freeze_time("2026-09-20"):  # a Sunday
        date = dt.date(2026, 9, 20) + dt.timedelta(days=delta_days)
        assert weekday_naturalday(date) == expected


def test_weekday_naturalday_falls_back_to_a_formatted_date_beyond_a_week():
    with freeze_time("2026-09-20"):
        date = dt.date(2026, 9, 20) + dt.timedelta(days=7)
        assert weekday_naturalday(date) == "Sept. 27, 2026"


@pytest.mark.parametrize(
    ["real_today", "as_of", "date", "expected"],
    [
        ["2026-09-01", "2026-09-24", dt.date(2026, 9, 25), "tomorrow"],
        ["2026-09-01", "2026-09-24", dt.date(2026, 9, 27), "on Sunday"],
    ],
    ids=[
        "as_of overrides real today for the tomorrow branch",
        "as_of overrides real today for the weekday branch",
    ],
)
def test_weekday_naturalday_as_of_overrides_the_real_today(real_today, as_of, date, expected):
    # OccurrencePreviewView renders as if "today" were the occurrence's
    # own send_date, which is almost always well before the real date
    # this test actually runs on - as_of has to win over the real clock
    # for the preview to be meaningful, not just a formality.
    with freeze_time(real_today):
        assert weekday_naturalday(date, dt.date.fromisoformat(as_of)) == expected
