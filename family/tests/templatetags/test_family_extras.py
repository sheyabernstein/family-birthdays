import datetime as dt

import pytest
from django.template.defaultfilters import date as date_filter
from django.utils import dateformat
from freezegun import freeze_time

from family.models import Person
from family.templatetags.family_extras import (
    display_name_with_marker,
    hebrew_str,
    weekday_naturalday,
    with_hebrew_first_name,
)

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


def test_with_hebrew_first_name_is_suppressed_when_display_name_is_already_hebrew(family):
    # display_name falls back to hebrew_name when there's no English
    # first name (see Person.display_name) - showing the parenthetical
    # on top of that would just repeat the same name back
    # ("בלומא ראקאך (בלומא)" instead of useful new information).
    person = Person.objects.create(family=family, last_name_en="Rokach", first_name_he="בלומא")
    assert with_hebrew_first_name(person) == ""


def test_with_hebrew_first_name_is_suppressed_for_a_hebrew_nickname(family):
    # display_name prefers nickname over everything else - a Hebrew
    # nickname makes display_name Hebrew script just as surely as the
    # no-English-first-name fallback does, even though a nickname is
    # technically set (see Person.display_name_is_hebrew's own
    # docstring for why this can't be a field-presence check).
    person = Person.objects.create(family=family, nickname="בלומי", first_name_he="בלומא")
    assert with_hebrew_first_name(person) == ""


def test_display_name_with_marker_wraps_an_english_name_with_an_isolated_marker(family):
    person = Person.objects.create(
        family=family, first_name_en="Yitschak", last_name_en="Bernstein", first_name_he="יצחק"
    )
    person.dod_gregorian = dt.date(2020, 1, 1)
    person.save()

    assert display_name_with_marker(person) == 'Yitschak Bernstein<span class="hebrew-suffix"> ע״ה</span>'


def test_display_name_with_marker_wraps_a_hebrew_name_as_one_rtl_unit(family):
    # Plain "{{ display_name }}{{ marker }}" concatenation with the
    # marker in its own isolated-RTL span only reads correctly when
    # display_name is English - a Hebrew display_name needs the whole
    # name+marker wrapped together as one RTL unit instead, or a reader
    # encounters the marker before the name (see this filter's own
    # docstring, confirmed via bounding-rect measurement in a real
    # browser during development, not just visual inspection).
    person = Person.objects.create(family=family, last_name_en="Rokach", first_name_he="בלומא")
    person.dod_gregorian = dt.date(2020, 1, 1)
    person.save()

    assert display_name_with_marker(person) == '<span class="name-rtl">בלומא ע״ה</span>'


def test_display_name_with_marker_omits_the_marker_for_a_living_person(family):
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")
    assert display_name_with_marker(person) == "Blimi Rokach"


def test_display_name_with_marker_handles_none():
    assert display_name_with_marker(None) == ""


@pytest.mark.parametrize(
    ["delta_days", "expected"],
    [
        [-1, "yesterday"],
        [0, "today"],
        [1, "tomorrow"],
        [2, "on Tuesday"],
        [6, "on Shabbos"],
        [-6, "on Monday"],
    ],
    ids=[
        "yesterday still uses naturalday's own word",
        "today still uses naturalday's own word",
        "tomorrow still uses naturalday's own word",
        "two days ahead names the weekday",
        "six days ahead lands on Shabbos",
        "six days ago names the weekday",
    ],
)
def test_weekday_naturalday_names_the_weekday_within_a_week(delta_days, expected):
    with freeze_time("2026-09-20"):  # a Sunday
        date = dt.date(2026, 9, 20) + dt.timedelta(days=delta_days)
        assert weekday_naturalday(date) == expected


def test_djangos_own_date_filter_renders_saturday_as_shabbos():
    # family.apps.FamilyConfig.ready() patches Django's own WEEKDAYS dict
    # (index 5 = Saturday) at process startup - covers every |date:"l"/"D"
    # call site in the app (ledger-stamp displays throughout) with no
    # per-template edits, but only for code that actually goes through
    # Django's date formatting.
    saturday = dt.date(2026, 9, 26)
    assert date_filter(saturday, "l") == "Shabbos"
    assert date_filter(saturday, "D") == "Shabbos"


def test_raw_strftime_still_says_saturday_not_shabbos():
    # The other half of the same regression guard: strftime reads the OS
    # locale tables, not Django's patched WEEKDAYS dict, so it was never
    # going to pick up "Shabbos" - this is exactly why weekday_naturalday
    # (and every other weekday-rendering call site) must go through
    # dateformat.format/the |date filter instead, never strftime/%A. See
    # AGENTS.md's "Dates" section.
    saturday = dt.date(2026, 9, 26)
    assert saturday.strftime("%A") == "Saturday"
    assert dateformat.format(saturday, "l") == "Shabbos"


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
