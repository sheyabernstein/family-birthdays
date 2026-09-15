import datetime as dt

from django import template
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
