import datetime as dt

from django import template
from django.utils import dateformat, formats, timezone
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
    "today" is a Shabbos/Yom Tov shift - at most family.hebrew.
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

    Renders the weekday via dateformat.format, not strftime - see
    family.apps.FamilyConfig.ready() and AGENTS.md's "Dates" section.
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
        return f"on {dateformat.format(date, 'l')}"
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

    Suppressed when display_name_is_hebrew - display_name is already
    Hebrew script in that case (most often the no-English-first-name
    fallback, but also a Hebrew nickname - see Person.display_name_is_
    hebrew), so appending "(first_name_he)" would just repeat the same
    name back, e.g. "בלומא ראקאך (בלומא)" instead of the intended
    "Blimi Rokach (בלומא)" for someone who actually has both.
    """
    if person is None or not person.first_name_he or person.display_name_is_hebrew:
        return SafeString("")
    return format_html(" ({})", person.first_name_he)


@register.filter
def display_name_with_marker(person: Person | None) -> SafeString:
    """Combines display_name + memorial_marker into one HTML unit, with correct bidi handling.

    Plain `"{{ display_name }}{{ marker }}"` concatenation (an isolated-
    RTL `.hebrew-suffix` span trailing plain display_name text) works
    fine when display_name is English - the marker's own isolated RTL
    span is embedded, in the natural place, within the surrounding LTR
    paragraph. It breaks once display_name is itself Hebrew (see
    Person.display_name_is_hebrew): display_name's own text has no
    direction/isolation of its own, so the browser auto-detects it as
    an RTL run *within* the outer LTR
    paragraph, and the isolated marker span (a separate embedded RTL
    object) ends up placed by the *outer* LTR paragraph's own embedding
    order - visually to the right of the name, which a Hebrew reader
    (right-to-left) encounters *before* the name instead of after it.
    Confirmed via bounding-rect measurement in a real browser, not just
    visual inspection - the bug isn't obvious from a screenshot alone.

    Wrapping the whole name+marker as one `direction: rtl; unicode-bidi:
    isolate;` unit (`.name-rtl`, not `.hebrew-suffix`) fixes this: it
    becomes a single RTL paragraph, so native bidi reordering keeps
    "name, then marker" in the correct right-to-left reading order,
    matching how `.hebrew-name` already isolates the secondary Hebrew-
    name line elsewhere on person_detail.html. Also picks up the same
    Noto Sans Hebrew font-family fix along the way - display_name text
    was otherwise falling back to whatever serif the OS has for Hebrew
    glyphs (neither of this app's own display/mono fonts ship them -
    see AGENTS.md's "Dates"-adjacent font note).
    """
    if person is None:
        return SafeString("")
    if person.display_name_is_hebrew:
        return format_html('<span class="name-rtl">{}{}</span>', person.display_name, person.memorial_marker)
    if not person.memorial_marker:
        return format_html("{}", person.display_name)
    return format_html('{}<span class="hebrew-suffix">{}</span>', person.display_name, person.memorial_marker)


@register.filter
def initials(person: Person) -> str:
    letters = (person.first_name_en[:1] + person.last_name_en[:1]).upper()
    return letters or person.first_name_he[:1] or "?"
