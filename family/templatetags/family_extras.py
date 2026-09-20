import datetime as dt

from django import template
from django.contrib.humanize.templatetags.humanize import naturalday
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

from family.hebrew import format_hebrew_date, gregorian_to_hebrew
from family.models import Person

register = template.Library()


@register.filter
def hebrew_str(date: dt.date | None) -> str:
    """Render a Gregorian date as its Hebrew-calendar equivalent, e.g. י״ד אדר תשפ״ז."""
    if date is None:
        return ""
    return format_hebrew_date(gregorian_to_hebrew(date))


@register.filter
def weekday_naturalday(date: dt.date) -> str:
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
    falls back to naturalday's own full-date formatting there.

    The "on" is part of this filter's own output, not the calling
    template's copy - "is today"/"is tomorrow" read right without one,
    but a bare weekday name ("is Monday") reads like a typo missing its
    preposition, so every caller just writes "is {{ ...|weekday_naturalday }}"
    uniformly and lets the filter decide.
    """
    delta = (date - timezone.localdate()).days
    if abs(delta) <= 1 or abs(delta) > 6:
        return naturalday(date)
    return f"on {date:%A}"


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
