import datetime as dt

import pytest
from hdate.hebrew_date import HebrewDate, Months

from family.hebrew import (
    format_hebrew_date,
    gregorian_to_hebrew,
    hebrew_to_gregorian,
    resolve_hebrew_anniversary,
    resolve_send_date,
)


def test_format_hebrew_date_omits_the_thousands_digit():
    # ה' means 5000 - always true for any date this app will ever compute,
    # so it's noise, not information.
    assert format_hebrew_date(HebrewDate(5786, Months.ADAR, 14)) == 'י"ד אדר תשפ"ו'


@pytest.mark.parametrize(
    ["year", "expected_date"],
    [
        [5785, dt.date(2025, 6, 11)],
        [5786, dt.date(2026, 5, 31)],
        [5787, dt.date(2027, 6, 20)],
        [5788, dt.date(2028, 6, 9)],
    ],
    ids=[
        "5785 is 2025-06-11",
        "5786 is 2026-05-31",
        "5787 is 2027-06-20 (leap year)",
        "5788 is 2028-06-09",
    ],
)
def test_simple_anniversary_round_trips_across_years(year, expected_date):
    # 15 Sivan across a run of years including a leap year (5787) -
    # month shouldn't be affected since Sivan isn't Adar.
    actual = hebrew_to_gregorian(
        resolve_hebrew_anniversary(anchor_month=Months.SIVAN, anchor_day=15, target_year=year)
    )
    assert actual == expected_date


@pytest.mark.parametrize(
    ["anchor_month", "adar_observance", "target_year", "expected_month"],
    [
        [Months.ADAR, "adar_ii", 5787, Months.ADAR_II],
        [Months.ADAR, "adar_i", 5787, Months.ADAR_I],
        [Months.ADAR_II, "adar_ii", 5788, Months.ADAR],
    ],
    ids=[
        "adar defaults to adar ii in a leap year",
        "adar respects adar i when configured",
        "adar ii collapses to adar in a non-leap year",
    ],
)
def test_adar_yahrzeit_observance(anchor_month, adar_observance, target_year, expected_month):
    hd = resolve_hebrew_anniversary(
        anchor_month=anchor_month, anchor_day=10, target_year=target_year, adar_observance=adar_observance
    )
    assert hd.month == expected_month
    assert hd.day == 10


@pytest.mark.parametrize(
    ["target_year", "day30_observance", "expected_month", "expected_day"],
    [
        [5786, "start_of_next_month", Months.KISLEV, 1],
        [5786, "last_day_of_month", Months.MARCHESHVAN, 29],
        [5785, "start_of_next_month", Months.MARCHESHVAN, 30],
    ],
    ids=[
        "short cheshvan falls back to 1 kislev by default",
        "short cheshvan falls back to its last day when configured",
        "long cheshvan keeps the 30th",
    ],
)
def test_30_cheshvan_observance(target_year, day30_observance, expected_month, expected_day):
    # 5786 is a year where Marcheshvan only has 29 days; 5785 is a year
    # where it has the full 30.
    hd = resolve_hebrew_anniversary(
        anchor_month=Months.MARCHESHVAN,
        anchor_day=30,
        target_year=target_year,
        day30_observance=day30_observance,
    )
    assert hd.month == expected_month
    assert hd.day == expected_day


@pytest.mark.parametrize(
    ["occurrence_date", "expected_send_date", "expected_reasons"],
    [
        [dt.date(2025, 9, 23), dt.date(2025, 9, 22), ["Yom Tov"]],
        [dt.date(2026, 6, 9), dt.date(2026, 6, 9), []],
    ],
    ids=[
        "yom tov shifts the send date a day earlier",
        "an ordinary weekday is unchanged",
    ],
)
def test_resolve_send_date(occurrence_date, expected_send_date, expected_reasons):
    send_date, reasons = resolve_send_date(occurrence_date)
    assert reasons == expected_reasons
    assert send_date == expected_send_date


def test_resolve_send_date_names_both_reasons_for_a_multi_day_chain():
    # 2 Tishrei 5787 (Rosh Hashanah day 2) - 1 Tishrei that year happens
    # to fall on Shabbos too, so walking back from day 2 crosses a day
    # that's both Shabbos and Yom Tov before reaching an ordinary Friday -
    # both reasons should be named, Shabbos first (sorted, not whichever
    # day the walk happened to hit first).
    occurrence_date = dt.date(2026, 9, 13)

    send_date, reasons = resolve_send_date(occurrence_date)

    assert send_date == dt.date(2026, 9, 11)
    assert reasons == ["Shabbos", "Yom Tov"]


def test_gregorian_hebrew_round_trip():
    original = dt.date(1990, 9, 22)
    hd = gregorian_to_hebrew(original)
    assert hebrew_to_gregorian(hd) == original
