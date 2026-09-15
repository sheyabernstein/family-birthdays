"""Hebrew-calendar math for life-event scheduling.

Two distinct problems live here:

1. Resolving a recurring Hebrew anniversary (birthday/yahrzeit/anniversary)
   for a *specific* Hebrew year, given only the anchor month/day. This is
   ambiguous in two well-known ways:
     - Adar in a leap year splits into Adar I and Adar II; custom
       determines which one an Adar anniversary is observed in.
     - Cheshvan and Kislev vary between 29 and 30 days; an anniversary that
       fell on the 30th doesn't exist in a year where the month is short.
   Both are configurable per-person/per-union because family customs vary.

2. Shifting a resolved Gregorian occurrence date to the actual notification
   send date: if the occurrence falls on Shabbat or Yom Tov, the
   notification needs to go out the day before, since nobody's checking
   phones that day. A Shabbat abutting a two-day Diaspora Yom Tov can chain
   multiple days back.
"""

from __future__ import annotations

import datetime as dt

from hdate import HDateInfo, HebrewDate
from hdate.gematria import hebrew_number
from hdate.hebrew_date import Months, is_leap_year

ADAR_MONTHS = {Months.ADAR, Months.ADAR_I, Months.ADAR_II}

# Max consecutive Shabbat/Yom-Tov days to walk back through (covers a
# Diaspora "three-day yontif": Yom Tov, Shabbat, Yom Tov).
MAX_SHIFT_DAYS = 4


def gregorian_to_hebrew(date: dt.date) -> HebrewDate:
    return HDateInfo(date).hdate


def hebrew_to_gregorian(hebrew_date: HebrewDate, diaspora: bool = True) -> dt.date:
    return HDateInfo(hebrew_date, diaspora=diaspora).gdate


def format_hebrew_date(date: HebrewDate, *, include_year: bool = True) -> str:
    """Renders a Hebrew date the way every user-facing display in this app shows one.

    hdate's own `str(HebrewDate(...))` includes the year's thousands
    digit (e.g. `ה' תשפ"ו` - the `ה'` meaning 5000), which is the same
    for every date this app will ever compute (every family record and
    every future Occurrence falls in the 5000s) and just adds noise.
    hdate's `HebrewDate.__str__` has no option to omit it, so this
    reimplements that same one-line format using `year % 1000` instead
    of the full year, via hdate's own `hebrew_number` (the same gematria
    formatter `HebrewDate.__str__` itself calls).

    Args:
        date: The Hebrew date to render.
        include_year: False renders day+month only (e.g. `ט"ו אדר`, no
            year at all) - used to show a person's birth month/day
            without their birth year to a viewer who shouldn't see it
            (see family.access.can_see_birth_year). `date.year` is still
            required to be a real, valid Hebrew year regardless - it's
            just never read into the output.

    Returns:
        The formatted Hebrew date string.
    """
    day = hebrew_number(date.day)
    if not include_year:
        return f"{day} {date.month}"
    year = hebrew_number(date.year % 1000)
    return f"{day} {date.month} {year}"


def resolve_anniversary_month(anchor_month: Months, target_year: int, adar_observance: str) -> Months:
    """Map an anchor Hebrew month onto a specific target year.

    Only matters when the anchor month was itself an Adar month: in a
    leap target year it becomes Adar I or Adar II per `adar_observance`
    ("adar_i" or "adar_ii"); in a non-leap target year it's just Adar.
    """
    if anchor_month not in ADAR_MONTHS:
        return anchor_month

    if is_leap_year(target_year):
        return Months.ADAR_I if adar_observance == "adar_i" else Months.ADAR_II
    return Months.ADAR


def resolve_hebrew_anniversary(
    *,
    anchor_month: Months,
    anchor_day: int,
    target_year: int,
    adar_observance: str = "adar_ii",
    day30_observance: str = "start_of_next_month",
) -> HebrewDate:
    """Resolve an anchor Hebrew month/day to a HebrewDate for a target year.

    Handles the Adar and 30-day-month ambiguities per the given
    observance customs.

    `day30_observance` is "start_of_next_month" (default - observe on the
    1st of the following month, the common Ashkenazi practice) or
    "last_day_of_month" (observe on the last day of the now-short month).
    """
    month = resolve_anniversary_month(anchor_month, target_year, adar_observance)
    max_day = month.days(target_year)

    if anchor_day <= max_day:
        return HebrewDate(target_year, month, anchor_day)

    # Anchor day was the 30th of a month that, in the target year, only has
    # 29 days. In practice this can only happen for Cheshvan/Kislev.
    if day30_observance == "last_day_of_month":
        return HebrewDate(target_year, month, max_day)

    next_month = month.next_month(target_year)
    next_year = target_year + 1 if next_month == Months.TISHREI else target_year
    return HebrewDate(next_year, next_month, 1)


def resolve_send_date(occurrence_date: dt.date, diaspora: bool = True) -> tuple[dt.date, bool]:
    """Walk backward from a halachic occurrence date while it lands on Shabbat or Yom Tov.

    Returns:
        A tuple of the resolved send date and whether it was actually
        shifted from occurrence_date.
    """
    date = occurrence_date
    shifted = False
    for _ in range(MAX_SHIFT_DAYS):
        info = HDateInfo(date, diaspora=diaspora)
        if not (info.is_shabbat or info.is_yom_tov):
            break
        date -= dt.timedelta(days=1)
        shifted = True
    return date, shifted
