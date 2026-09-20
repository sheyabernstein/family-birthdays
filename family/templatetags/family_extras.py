import datetime as dt

from django import template
from django.utils import formats, timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext

from family.hebrew import format_hebrew_date, gregorian_to_hebrew
from family.models import Person

register = template.Library()


@register.filter
def hebrew_str(date: dt.date | None, include_year: bool = True) -> str:
    """Render a Gregorian date as its Hebrew-calendar equivalent, e.g. י״ד אדר תשפ״ז.

    include_year=False drops the year entirely (e.g. י״ד אדר) - used for
    the SMS body's own inline date, where every extra character costs
    real budget (see notifications.tasks.SMS_CHAR_BUDGET) and the year
    is exactly the kind of thing this app already treats as noise (see
    format_hebrew_date's own docstring on why the thousands digit is
    dropped even when the year *is* shown).
    """
    if date is None:
        return ""
    return format_hebrew_date(gregorian_to_hebrew(date), include_year=include_year)


@register.filter
def weekday_naturalday(date: dt.date, as_of: dt.date | None = None) -> str:
    """Like humanize's naturalday, but names the weekday for a 2-6 day gap.

    naturalday only has words for a same-day/yesterday/tomorrow gap and
    falls back to a full formatted date ("September 28") for anything
    further out. The only gap this app can actually produce ahead of
    "today" is a Shabbat/Yom Tov shift - at most family.hebrew.
    MAX_SHIFT_DAYS (4) days - where "is on Monday" reads far more
    naturally than "is September 28". Beyond a week (only possible for a
    genuinely late catch-up send, never a shift - see notifications.
    tasks.MAX_CATCHUP_DAYS_LATE, well under a week itself) a bare
    weekday name would be ambiguous about which week, so this still
    falls back to the same full-date formatting naturalday itself uses.

    The "on" is part of this filter's own output, not the calling
    template's copy - "is today"/"is tomorrow" read right without one,
    but a bare weekday name ("is Monday") reads like a typo missing its
    preposition, so every caller just writes "is {{ ...|weekday_naturalday }}"
    uniformly and lets the filter decide.

    This deliberately doesn't delegate to naturalday itself, unlike an
    earlier version - naturalday hardcodes datetime.date.today() with no
    way to override it, and notifications.tasks needs to render an
    occurrence's copy as it will actually read on its real send_date (a
    preview shown well before that date otherwise), not as of whenever
    the preview happens to be requested. as_of covers that: pass the
    date to treat as "today", or omit it for the real one. (An
    alternative - actually mocking "today" via something like freezegun
    around the render call - was ruled out: it patches the process-wide
    clock for the duration of the call, which under a multi-threaded
    worker could leak into a concurrent, unrelated request computing a
    real send_date at the same moment - not a risk worth taking in an
    app whose entire scheduling model depends on "today" being right.)
    """
    today = as_of or timezone.localdate()
    delta = (date - today).days
    if delta == 0:
        return gettext("today")
    if delta == 1:
        return gettext("tomorrow")
    if delta == -1:
        return gettext("yesterday")
    if -6 <= delta <= 6:
        return f"on {date:%A}"
    return formats.date_format(date)


@register.filter
def with_hebrew_first_name(person: Person | None) -> SafeString:
    """Trailing " (HebrewFirstName)" parenthetical, or empty if there isn't one.

    Meant to follow right after a person's own English display_name -
    e.g. `<strong>{{ occurrence.person.display_name }}</strong>{{
    occurrence.person|with_hebrew_first_name }}` - kept as its own
    filter rather than repeated inline in every event-type email
    template (birthday/yahrzeit/bar+bat mitzvah/wedding/anniversary/
    default all need it, for both people on a Union-anchored one).
    """
    if person is None or not person.first_name_he:
        return SafeString("")
    return format_html(" ({})", person.first_name_he)


@register.filter
def initials(person: Person) -> str:
    letters = (person.first_name_en[:1] + person.last_name_en[:1]).upper()
    return letters or person.first_name_he[:1] or "?"
