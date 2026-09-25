import datetime as dt
from collections import defaultdict
from collections.abc import Iterator
from urllib.parse import quote

from celery import Task, shared_task
from django.db import models
from django.template.loader import render_to_string
from django.utils import timezone
from hdate.hebrew_date import Months

from config.enums import TaskPriority
from config.logging_config import logger
from config.observability import metrics
from family.hebrew import (
    gregorian_to_hebrew,
    hebrew_to_gregorian,
    resolve_hebrew_anniversary,
    resolve_send_date,
)
from family.models import Person, Union
from family.templatetags.family_extras import weekday_naturalday
from notifications.audience import resolve_audience, resolve_broadcast_audience
from notifications.enums import ChannelEnum
from notifications.helpers import html_to_plain_text
from notifications.models import Broadcast, EventType, Message, Occurrence
from notifications.services import IDENTIFIER_PLACEHOLDER, send_email, send_sms
from notifications.sms import SmsRateLimitedError, SmsUnrecoverableError

# A single GSM-7 SMS segment - see AGENTS.md. Deliberately conservative
# rather than budgeting for 2-segment messages: forces genuinely terse
# copy and never risks a family member being charged for (or a flaky
# carrier splitting) a multi-part text over what should be one line.
SMS_CHAR_BUDGET = 160

# How far ahead to keep Occurrence rows computed. Re-running the nightly
# job is idempotent (update_or_create on the unique constraint), so this
# self-heals if a person's dates are corrected later or a new EventType
# is added.
OCCURRENCE_HORIZON_DAYS = 400

# How many days past the real occurrence_date a catch-up send (send_due_
# notifications picking up a row a missed run left behind - see that
# task's own docstring) is still worth sending. Beyond this, "was on
# <date>" stops reading as a timely nudge and starts reading as stale
# noise nobody asked for, so the occurrence is discarded (deleted, no
# message ever queued) rather than sent late. Deliberately smaller than
# family.hebrew.MAX_SHIFT_DAYS (4) - a Shabbos/Yom Tov shift is never
# "late" in the first place (occurrence_date hasn't passed yet), so the
# two constants aren't meant to line up.
MAX_CATCHUP_DAYS_LATE = 3

# A girl's 12th Hebrew birthday and a boy's 13th are a bat/bar mitzvah,
# not just another birthday - these codes are never computed as their own
# independent yearly event (see the exclusion in _event_types_by_family);
# they only ever replace that one specific year's "birthday" occurrence,
# via _coming_of_age_code below.
BAT_MITZVAH_AGE = 12
BAR_MITZVAH_AGE = 13
COMING_OF_AGE_CODES = {EventType.BuiltinCode.BAR_MITZVAH, EventType.BuiltinCode.BAT_MITZVAH}

# Broadcast isn't anchor/Occurrence-driven at all - it's sent directly by
# send_due_broadcasts below, on its own send_at timestamp rather than a
# computed yearly recurrence. Excluded here for the same reason
# COMING_OF_AGE_CODES is: so it's never treated as an independent
# per-subject event to schedule (it'd no-op anyway, since _anchor_for has
# no case for it and returns no anchor - this just makes that explicit).
NON_SCHEDULED_CODES = COMING_OF_AGE_CODES | {EventType.BuiltinCode.BROADCAST}


def _anchor_for(
    subject: Person | Union, event_type: EventType
) -> tuple[tuple[Months, int] | None, str, str, int | None]:
    """Resolves the anchor date info an EventType needs to schedule for a subject.

    Returns:
        A tuple of:

        - The month/day anchor, or None if this event type isn't
          anchor-driven.
        - The Adar observance to use.
        - The day-30 observance to use.
        - The Hebrew year the anchor event itself happened in, if known
          - the floor _compute_for_subject uses so nothing fires before
            the event it's celebrating has actually happened, e.g. a
            Union whose wedding is still in the future shouldn't get an
            "anniversary" this year just because the month/day happens
            to match (see Union.is_upcoming and AGENTS.md).
    """
    if event_type.anchor == EventType.Anchor.BIRTH:
        return (
            subject.dob_hebrew_anchor,
            subject.yahrzeit_adar_observance,
            subject.yahrzeit_day30_observance,
            subject.dob_hebrew_year,
        )
    if event_type.anchor == EventType.Anchor.DEATH:
        return (
            subject.dod_hebrew_anchor,
            subject.yahrzeit_adar_observance,
            subject.yahrzeit_day30_observance,
            subject.dod_hebrew_year,
        )
    if event_type.anchor == EventType.Anchor.MARRIAGE:
        return subject.marriage_hebrew_anchor, "adar_ii", "start_of_next_month", subject.marriage_hebrew_year
    if event_type.anchor == EventType.Anchor.ENGAGEMENT:
        return (
            subject.engagement_hebrew_anchor,
            "adar_ii",
            "start_of_next_month",
            subject.engagement_hebrew_year,
        )
    return None, "adar_ii", "start_of_next_month", None


def _event_types_by_family() -> (
    tuple[defaultdict[int | None, list[EventType]], defaultdict[int | None, list[EventType]]]
):
    """Builds scheduled EventTypes grouped by family and by subject kind.

    Returns:
        A (person_types_by_family_id, union_types_by_family_id) tuple,
        each also keyed under None for the global defaults every family
        gets.
    """
    person_types: defaultdict[int | None, list[EventType]] = defaultdict(list)
    union_types: defaultdict[int | None, list[EventType]] = defaultdict(list)
    for event_type in EventType.objects.exclude(code__in=NON_SCHEDULED_CODES):
        bucket = union_types if event_type.applies_to_union else person_types
        bucket[event_type.family_id].append(event_type)
    return person_types, union_types


def union_is_eligible_for_notifications(union: Union) -> bool:
    """Whether a union's events should fire at all.

    A union's events only ever fire while it's actually a live marriage
    between two people who are both still being tracked - an untracked
    spouse (notifications_enabled=False - a lineage-only stub, e.g. an
    in-law's own parent entered just so the tree renders) means nobody
    should ever be notified about this marriage either, same as a
    Person's own birthday/yahrzeit already stop for an untracked Person
    (see Person.notifications_enabled in AGENTS.md).
    """
    return (
        union.status == Union.Status.MARRIED
        and union.person_a.is_living
        and union.person_b.is_living
        and union.person_a.notifications_enabled
        and union.person_b.notifications_enabled
    )


def _subject_pairs() -> Iterator[tuple[Person | Union, EventType]]:
    """Yields every subject/event-type pair that could ever need scheduling.

    Every Person with a birth/death anchor, and every currently-married
    Union with a marriage anchor. This is deliberately not
    subscription-driven: who gets *notified* is a separate question (see
    notifications.audience) from whether the event happens at all.

    An untracked (notifications_enabled=False) Person is normally
    excluded entirely - a lineage-only stub, e.g. an in-law's own parent,
    has nothing to schedule. The one exception is an EventType with
    always_schedule=True (Yahrzeit): a real ancestor entered only as a
    stub can still die, and their yahrzeit should still be computable for
    whoever actually wants it (see AGENTS.md) - excluding untracked people
    from the query outright would make that impossible regardless of what
    happens later in this function, so the DB filter below only excludes
    a *living* untracked person (always_schedule only ever matters for a
    DEATH-anchored type, which needs is_living=False anyway).
    """
    person_types, union_types = _event_types_by_family()

    for person in Person.objects.filter(models.Q(notifications_enabled=True) | models.Q(is_living=False)):
        for event_type in person_types[None] + person_types.get(person.family_id, []):
            if not event_type.always_schedule and not person.notifications_enabled:
                continue
            if event_type.anchor == EventType.Anchor.DEATH and person.is_living:
                continue
            # Symmetric to the DEATH-anchor check above: once someone has
            # died there's no more birthday (or bar/bat mitzvah, which
            # only ever supersedes a birthday - see _coming_of_age_code)
            # to celebrate, only the yahrzeit going forward.
            if event_type.anchor == EventType.Anchor.BIRTH and not person.is_living:
                continue
            yield person, event_type

    for union in Union.objects.filter(status=Union.Status.MARRIED).select_related("person_a", "person_b"):
        if not union_is_eligible_for_notifications(union):
            continue
        applicable = (
            union_types[None]
            + union_types.get(union.person_a.family_id, [])
            + union_types.get(union.person_b.family_id, [])
        )
        for event_type in applicable:
            yield union, event_type


def _event_types_for_person(person: Person) -> Iterator[EventType]:
    if (
        not person.notifications_enabled
        and not EventType.objects.filter(
            models.Q(family__isnull=True) | models.Q(family_id=person.family_id), always_schedule=True
        )
        .exclude(code__in=NON_SCHEDULED_CODES)
        .exists()
    ):
        # Fast path for the common case - an untracked lineage-only stub
        # with no always_schedule type in play has nothing to compute at
        # all, so this skips building the full per-family EventType
        # grouping below (_event_types_by_family fetches and buckets
        # every row) in favor of one indexed existence check. Matters
        # because compute_occurrences_for_person calls this on every
        # single Person save via family.signals, tracked or not - a
        # short-circuited `and` means this .exists() call never even
        # runs for a tracked person, so it costs nothing in the common
        # (tracked) case either.
        return
    person_types, _union_types = _event_types_by_family()
    for event_type in person_types[None] + person_types.get(person.family_id, []):
        # Same always_schedule exception as _subject_pairs above.
        if not event_type.always_schedule and not person.notifications_enabled:
            continue
        if event_type.anchor == EventType.Anchor.DEATH and person.is_living:
            continue
        if event_type.anchor == EventType.Anchor.BIRTH and not person.is_living:
            continue
        yield event_type


def _event_types_for_union(union: Union) -> Iterator[EventType]:
    if not union_is_eligible_for_notifications(union):
        return
    _person_types, union_types = _event_types_by_family()
    yield from (
        union_types[None]
        + union_types.get(union.person_a.family_id, [])
        + union_types.get(union.person_b.family_id, [])
    )


def _coming_of_age_code(person: Person, hebrew_year: int) -> str | None:
    """Resolves the bat/bar mitzvah code for a person's Hebrew birthday, if applicable.

    A girl's 12th Hebrew birthday, or a boy's 13th, is a bat/bar mitzvah
    instead of an ordinary birthday. Only decided when the Hebrew
    birth *year* was actually entered - deriving it from the Gregorian
    date isn't something this app does for anchor dates (see
    family.hebrew and AGENTS.md), and guessing wrong here would put the
    wrong event type on a real simcha.
    """
    if not person.dob_hebrew_year:
        return None
    age = hebrew_year - person.dob_hebrew_year
    if person.gender == Person.Gender.FEMALE and age == BAT_MITZVAH_AGE:
        return EventType.BuiltinCode.BAT_MITZVAH
    if person.gender == Person.Gender.MALE and age == BAR_MITZVAH_AGE:
        return EventType.BuiltinCode.BAR_MITZVAH
    return None


def person_has_passed_coming_of_age(person: Person) -> bool:
    """Whether a Bar/Bat Mitzvah toggle for this person is already stale.

    True once they've passed the relevant age. Prefers the Hebrew birth year (the
    same math as _coming_of_age_code above) over Person.age, since age is
    Gregorian-only and is None for anyone with just a Hebrew birth year -
    a real, common case here, not an edge case to shrug off. Returns
    False (i.e. "still show it") when neither is known - there's no
    actual age to compare against, so hiding it would just as easily be
    wrong the other way.
    """
    threshold = BAT_MITZVAH_AGE if person.gender == Person.Gender.FEMALE else BAR_MITZVAH_AGE
    if person.dob_hebrew_year:
        today_hebrew_year = gregorian_to_hebrew(timezone.localdate()).year
        return today_hebrew_year - person.dob_hebrew_year >= threshold
    if person.age is not None:
        return person.age >= threshold
    return False


def _sibling_event_type(event_type: EventType, code: str) -> EventType | None:
    """Resolves the family's own override of `code`, falling back to the global default.

    The same two-tier lookup every other EventType use in this app
    follows.
    """
    return (
        EventType.objects.filter(family_id=event_type.family_id, code=code).first()
        or EventType.objects.filter(family__isnull=True, code=code).first()
    )


def _clear_superseded_occurrence_types(
    person: Person, hebrew_year: int, *, keep_event_type: EventType
) -> None:
    """Deletes stale Birthday/Bar/Bat Mitzvah occurrences superseded by `keep_event_type`.

    Birthday/bar-mitzvah/bat-mitzvah are mutually exclusive for one
    person in one Hebrew year - only one of the three should ever have a
    row. Cleans up whichever of the other two might be left over from
    before a gender or birth-year correction. Never touches an
    already-sent row; that's history, not a schedule.
    """
    Occurrence.objects.filter(
        person=person,
        hebrew_year=hebrew_year,
        event_type__code__in={EventType.BuiltinCode.BIRTHDAY} | COMING_OF_AGE_CODES,
        is_sent=False,
    ).exclude(event_type=keep_event_type).delete()


def _compute_for_subject(
    subject: Person | Union, event_type: EventType, *, horizon: dt.date, today_hebrew_year: int
) -> tuple[int, set[int]]:
    """Upserts this subject's Occurrence rows for one event type out to the horizon.

    Returns:
        A (rows written, ids of EventTypes actually used) tuple - the
        latter is usually just {event_type.id}, except a birthday year
        that resolves to a bar/bat mitzvah instead.
    """
    anchor, adar_obs, day30_obs, anchor_year = _anchor_for(subject, event_type)
    if not anchor:
        logger.debug("occurrence skip - no anchor date", subject=str(subject), event_type=event_type.code)
        return 0, set()
    if not event_type.recurs and anchor_year is None:
        # A one-time event type (e.g. Wedding) needs the actual anchor
        # year to know which single year it belongs to - without it there's
        # no way to tell this from any other year's month/day match.
        logger.debug(
            "occurrence skip - no anchor year for non-recurring type",
            subject=str(subject),
            event_type=event_type.code,
        )
        return 0, set()
    anchor_month, anchor_day = anchor

    is_person = isinstance(subject, Person)
    written = 0
    used_event_type_ids = set()
    for hebrew_year in range(today_hebrew_year, today_hebrew_year + 3):
        # Never fire before the anchor event itself happened - a birthday/
        # yahrzeit/anniversary doesn't recur before it first occurred, and
        # a non-recurring type (Wedding) only ever fires in that one exact
        # year, not every year its month/day comes around.
        if anchor_year is not None:
            if event_type.recurs and hebrew_year < anchor_year:
                continue
            if not event_type.recurs and hebrew_year != anchor_year:
                continue

        hd = resolve_hebrew_anniversary(
            anchor_month=anchor_month,
            anchor_day=anchor_day,
            target_year=hebrew_year,
            adar_observance=adar_obs,
            day30_observance=day30_obs,
        )
        occurrence_date = hebrew_to_gregorian(hd)
        if occurrence_date > horizon:
            continue
        # today_hebrew_year covers the whole current Hebrew year, but an
        # anchor early in it (Tishrei-Kislev) can resolve to a Gregorian
        # date months in the past - never write a new occurrence for
        # something that's already happened.
        if occurrence_date < timezone.localdate():
            continue

        notify_from = occurrence_date - dt.timedelta(days=event_type.notify_days_before)
        send_date, shift_reasons = resolve_send_date(notify_from)

        effective_event_type = event_type
        if is_person and event_type.code == EventType.BuiltinCode.BIRTHDAY:
            coming_of_age_code = _coming_of_age_code(subject, hd.year)
            if coming_of_age_code:
                effective_event_type = _sibling_event_type(event_type, coming_of_age_code) or event_type

        Occurrence.objects.update_or_create(
            person=subject if is_person else None,
            union=None if is_person else subject,
            event_type=effective_event_type,
            hebrew_year=hd.year,
            defaults={
                "occurrence_date": occurrence_date,
                "send_date": send_date,
                "shift_reasons": shift_reasons,
            },
        )
        written += 1
        used_event_type_ids.add(effective_event_type.id)

        if is_person and event_type.code == EventType.BuiltinCode.BIRTHDAY:
            _clear_superseded_occurrence_types(subject, hd.year, keep_event_type=effective_event_type)

    return written, used_event_type_ids


@shared_task(queue=TaskPriority.LOW)
def compute_occurrences() -> None:
    """Ensures every (person/union, event_type) pair has Occurrence rows out to the horizon.

    Runs nightly as a self-healing full sweep - see
    compute_occurrences_for_person/_union for the immediate,
    single-subject version called right after an edit. Background work,
    never a human waiting on it - see config.enums.TaskPriority.
    """
    logger.debug("compute_occurrences starting", horizon_days=OCCURRENCE_HORIZON_DAYS)
    horizon = timezone.localdate() + dt.timedelta(days=OCCURRENCE_HORIZON_DAYS)
    today_hebrew_year = gregorian_to_hebrew(timezone.localdate()).year

    created_or_updated = 0
    for subject, event_type in _subject_pairs():
        written, _event_type_ids = _compute_for_subject(
            subject, event_type, horizon=horizon, today_hebrew_year=today_hebrew_year
        )
        created_or_updated += written

    logger.info("occurrences computed", count=created_or_updated)
    metrics.beat_task_last_success_timestamp.labels(task_name="compute_occurrences").set(
        timezone.now().timestamp()
    )


def _delete_stale_unsent_occurrences(
    *, person: Person | None = None, union: Union | None = None, keep_event_type_ids: set[int]
) -> None:
    """Drops not-yet-sent occurrences for event types that no longer apply.

    E.g. a union that just became divorced shouldn't keep an
    already-computed anniversary reminder around. Never touches an
    already-sent row; that's history, not a schedule - is_sent=False is
    the only condition that matters here, deliberately not also
    send_date__gte=today (a previous version of this filter used that,
    and it was a real bug, not a refinement): resolve_send_date walks a
    notification backward across Shabbos/Yom Tov, so an occurrence
    computed on the anchor date itself, when that date is Yom Tov, gets a
    send_date already in the past the moment it's created - excluding
    "the past" here made exactly that row permanently un-cleanable, the
    same way it made it permanently un-sendable in send_due_notifications
    (see that function's own docstring). _clear_superseded_occurrence_
    types, doing the same kind of cleanup for birthday/bar/bat-mitzvah,
    never had this bug - it only ever filtered on is_sent=False.
    """
    qs = Occurrence.objects.filter(is_sent=False)
    qs = qs.filter(person=person) if person is not None else qs.filter(union=union)
    deleted, _ = qs.exclude(event_type_id__in=keep_event_type_ids).delete()
    if deleted:
        logger.info("stale occurrences removed", subject=str(person or union), count=deleted)
    else:
        logger.debug("no stale occurrences to remove", subject=str(person or union))


def compute_occurrences_for_person(person: Person) -> None:
    """Recomputes just this person's occurrences.

    Called right after they're created/edited so their birthday/yahrzeit
    shows up immediately instead of waiting for the nightly sweep.
    """
    logger.debug("recomputing person occurrences", person=person.uuid)
    horizon = timezone.localdate() + dt.timedelta(days=OCCURRENCE_HORIZON_DAYS)
    today_hebrew_year = gregorian_to_hebrew(timezone.localdate()).year
    applicable_event_type_ids = set()
    for event_type in _event_types_for_person(person):
        if _anchor_for(person, event_type)[0]:
            applicable_event_type_ids.add(event_type.id)
            _written, used_ids = _compute_for_subject(
                person, event_type, horizon=horizon, today_hebrew_year=today_hebrew_year
            )
            applicable_event_type_ids.update(used_ids)
    _delete_stale_unsent_occurrences(person=person, keep_event_type_ids=applicable_event_type_ids)
    logger.debug(
        "person occurrences recomputed",
        person=person.uuid,
        applicable_event_type_count=len(applicable_event_type_ids),
    )


def compute_occurrences_for_union(union: Union) -> None:
    """Same as compute_occurrences_for_person, for a marriage."""
    logger.debug("recomputing union occurrences", union=union.uuid)
    horizon = timezone.localdate() + dt.timedelta(days=OCCURRENCE_HORIZON_DAYS)
    today_hebrew_year = gregorian_to_hebrew(timezone.localdate()).year
    applicable_event_type_ids = set()
    for event_type in _event_types_for_union(union):
        if _anchor_for(union, event_type)[0]:
            applicable_event_type_ids.add(event_type.id)
            _written, used_ids = _compute_for_subject(
                union, event_type, horizon=horizon, today_hebrew_year=today_hebrew_year
            )
            applicable_event_type_ids.update(used_ids)
    _delete_stale_unsent_occurrences(union=union, keep_event_type_ids=applicable_event_type_ids)
    logger.debug(
        "union occurrences recomputed",
        union=union.uuid,
        applicable_event_type_count=len(applicable_event_type_ids),
    )


@shared_task(queue=TaskPriority.LOW)
def send_due_notifications() -> None:
    """Sends every due, unsent Occurrence's notifications.

    Filters send_date__lte, not send_date=, so a day this didn't run (an
    outage, a worker crash) or a row whose send_date landed in the past the
    moment it was computed - resolve_send_date walks notify_days_before
    backward across Shabbos/Yom Tov, and an immediate single-subject
    recompute (compute_occurrences_for_person/_union, triggered by an
    edit made that same day) can land exactly on the anchor date while
    that date is itself Yom Tov, shifting send_date to before "today" on
    arrival - both still go out on the next run instead of being silently
    skipped forever. Matches send_due_broadcasts' own send_at__lte for
    the same self-healing reason.

    Each occurrence is claimed via an update() affecting is_sent 0->1 before
    it's processed, not after - the same reasoning as send_due_broadcasts'
    own claim: without it, a worker crash/retry between sending and saving
    is_sent, or an overlapping run, would re-send the same occurrence's
    notifications to the whole family.

    A claimed occurrence more than MAX_CATCHUP_DAYS_LATE past its own
    occurrence_date is discarded (deleted outright) instead of sent - see
    that constant's own docstring.
    """
    today = timezone.localdate()
    due_ids = list(
        Occurrence.objects.filter(send_date__lte=today, is_sent=False).values_list("pk", flat=True)
    )
    logger.debug("send_due_notifications starting", due_count=len(due_ids))

    queued = 0
    claimed_count = 0
    discarded_count = 0
    for occurrence_id in due_ids:
        claimed = Occurrence.objects.filter(pk=occurrence_id, is_sent=False).update(is_sent=True)
        if not claimed:
            continue
        claimed_count += 1

        occurrence = Occurrence.objects.with_related().get(pk=occurrence_id)

        if occurrence.occurrence_date < today - dt.timedelta(days=MAX_CATCHUP_DAYS_LATE):
            logger.info(
                "discarding occurrence beyond the catch-up window",
                occurrence=occurrence.uuid,
                occurrence_date=occurrence.occurrence_date,
                days_late=(today - occurrence.occurrence_date).days,
            )
            discarded_count += 1
            occurrence.delete()
            continue

        audience = resolve_audience(
            event_type=occurrence.event_type, person=occurrence.person, union=occurrence.union
        )
        # Rendered once per channel actually needed for this occurrence,
        # not once per recipient - most occurrences have several
        # recipients on the same channel, and re-rendering the same
        # template for each would just be wasted work.
        rendered: dict[str, tuple[str, str, str]] = {}
        for account, channel, destination in audience:
            if channel not in rendered:
                rendered[channel] = _render_occurrence_message(occurrence, channel=channel)
            subject, body, html_body = rendered[channel]

            message = Message.objects.create(
                occurrence=occurrence,
                account=account,
                channel=channel,
                destination=destination,
                subject=subject,
                body=body,
                html_body=_personalize(html_body, destination=destination),
            )
            send_message.delay(message.pk)
            queued += 1

    logger.info(
        "notifications queued",
        occurrences_due=claimed_count,
        messages_queued=queued,
        occurrences_discarded=discarded_count,
    )
    metrics.beat_task_last_success_timestamp.labels(task_name="send_due_notifications").set(
        timezone.now().timestamp()
    )


def _truncate_for_sms(text: str, budget: int = SMS_CHAR_BUDGET) -> str:
    if len(text) <= budget:
        return text
    return text[: budget - 1].rstrip() + "…"


def _personalize(html_body: str, *, destination: str) -> str:
    """Swaps the "Manage notification settings" link's placeholder identifier for the real one.

    See services.IDENTIFIER_PLACEHOLDER's own docstring for why this is a
    plain string .replace() on the already-rendered html_body rather than
    a fresh per-recipient template render - html_body is empty for SMS
    (no footer link there), so this is a no-op in that case. quote() the
    destination first - it's landing inside a URL's querystring, and an
    Account.phone destination is E.164 ("+15551234567"): an un-encoded
    "+" in a querystring is itself the standard encoding for a literal
    space, so the phone number would come back mangled on the other end
    without this.
    """
    return html_body.replace(IDENTIFIER_PLACEHOLDER, quote(destination)) if html_body else html_body


def _occurrence_template_context(occurrence: Occurrence, *, as_of: dt.date | None = None) -> dict:
    family = occurrence.person.family if occurrence.person else occurrence.union.person_a.family
    today = as_of or timezone.localdate()
    # occurrence_date < today only when this send is genuinely late (a
    # missed run, an outage - see send_due_notifications' own docstring),
    # not when it's early for Shabbos/Yom Tov (occurrence_date > today,
    # already called out separately via shifted_for_shabbat_or_yomtov) -
    # templates use this to pick "was"/"is" tense, then say *when* via
    # the occurrence_date|weekday_naturalday filter rather than
    # hardcoding "Today is ..." - a real bug once shipped to production:
    # a birthday whose real Hebrew date fell on Yom Tov got shifted a
    # day earlier by resolve_send_date, so it was neither late nor
    # actually today, and every template's "not late" branch
    # unconditionally claimed "Today is ..." anyway. weekday_naturalday
    # reads "today"/"tomorrow"/"yesterday" for a 1-day gap either
    # direction, a weekday name ("Monday") for the 2-6 day gap a
    # Shabbos/Yom Tov shift can actually produce, and falls back to a
    # full formatted date beyond that - so the same filter covers the
    # on-time case and the shifted-early case without a separate flag
    # for it.
    #
    # "today" is also passed into the context (rather than templates
    # calling weekday_naturalday with no argument, defaulting to the
    # real one themselves) so a preview can render as of the occurrence's
    # own send_date instead of whenever the preview happens to be
    # requested - see OccurrencePreviewView and weekday_naturalday's own
    # docstring for why that's an explicit as_of, not a mocked clock.
    return {
        "occurrence": occurrence,
        "family_name": family.name,
        "is_late": occurrence.occurrence_date < today,
        "today": today,
    }


def _occurrence_subject(occurrence: Occurrence, *, is_late: bool, today: dt.date) -> str:
    """Builds the email subject line for one occurrence.

    Mirrors the body templates' own on-time/late/coming-up wording (see
    _occurrence_template_context's is_late, and its weekday_naturalday
    use) so the subject line never disagrees with the body it's paired
    with - a subject claiming "today" over a body that says "was on
    <date>" (a late catch-up send) or "coming up" (Wedding, sent
    notify_days_before ahead of the day itself) would be a confusing
    mismatch for whoever's just glancing at their inbox.
    """
    subject_obj = occurrence.person or occurrence.union
    name = getattr(subject_obj, "display_name", str(subject_obj))
    event_name = occurrence.event_type.name
    if is_late:
        return f"{name} - {event_name} was {weekday_naturalday(occurrence.occurrence_date, today)}"
    if occurrence.event_type.code == EventType.BuiltinCode.WEDDING:
        return f"{name} - {event_name} coming up"
    return f"{name} - {event_name} is {weekday_naturalday(occurrence.occurrence_date, today)}"


def _render_occurrence_message(
    occurrence: Occurrence, *, channel: str, as_of: dt.date | None = None
) -> tuple[str, str, str]:
    """Renders (subject, body, html_body) for one occurrence on one channel.

    Each event type gets its own template per channel (falling back to
    _default for a family's own custom event type - see
    notifications/templates/notifications/{email,sms}/) - the actual
    subject/copy is computed here from the occurrence's own relationships
    (person/union, event_type, dates) rather than duplicated per type in
    Python, so a birthday and a yahrzeit can read (and, for email, look)
    genuinely differently without more branching here.

    Email gets subject + a real HTML body + a plain-text fallback derived
    from that HTML (not hand-authored twice - see
    notifications.services.send_email for why that's enough). SMS has no
    subject and is rendered from its own short, plain-text template,
    truncated defensively to SMS_CHAR_BUDGET in case an unusually long
    name pushes it over.

    Args:
        occurrence: The occurrence to render a message for.
        channel: Which ChannelEnum to render for.
        as_of: The date to treat as "today" - the real send_date for an
            actual send (the default, real timezone.localdate()), or an
            occurrence's own send_date for a preview rendered ahead of
            time (see OccurrencePreviewView).
    """
    context = _occurrence_template_context(occurrence, as_of=as_of)
    if channel == ChannelEnum.EMAIL:
        html = render_to_string(
            [f"notifications/email/{occurrence.event_type.code}.html", "notifications/email/_default.html"],
            context,
        )
        body = html_to_plain_text(html)
        subject = _occurrence_subject(occurrence, is_late=context["is_late"], today=context["today"])
        return subject, body, html

    text = render_to_string(
        [f"notifications/sms/{occurrence.event_type.code}.txt", "notifications/sms/_default.txt"], context
    )
    text = _truncate_for_sms(" ".join(text.split()))
    return "", text, ""


def _broadcast_event_type(family_id: int) -> EventType:
    """Resolves this family's own override of the broadcast event type, or the global default.

    Same two-tier lookup as _sibling_event_type above. Falls back to
    creating the global default
    on the fly (rather than raising) in case a test/dev database was set
    up without running the seed migration.
    """
    return (
        EventType.objects.filter(family_id=family_id, code=EventType.BuiltinCode.BROADCAST).first()
        or EventType.objects.filter(family__isnull=True, code=EventType.BuiltinCode.BROADCAST).first()
        or EventType.objects.create(
            family=None, code=EventType.BuiltinCode.BROADCAST, name="Broadcast", anchor="", recurs=False
        )
    )


def _broadcast_subject(broadcast: Broadcast, people: list[Person]) -> str:
    family_name = broadcast.family.name
    if people:
        # A broadcast isn't "about" a person's own event the way an
        # occurrence is (see Person.memorial_marker's own callers) - a
        # tagged person here is a passing mention, not this message's
        # subject in that sense, so their memorial_marker still applies.
        names = ", ".join(f"{person.display_name}{person.memorial_marker}" for person in people)
        return f"{family_name} update - {names}"
    return f"{family_name} update"


def _render_broadcast_message(
    broadcast: Broadcast, people: list[Person], *, channel: str
) -> tuple[str, str, str]:
    """Renders (subject, body, html_body) for one broadcast on one channel.

    created_by is tracked on the Broadcast row itself (who to ask about
    it, visible on the Broadcasts page) but deliberately left out of the
    message a recipient actually receives - it's the family speaking, not
    an individual sender. broadcast.text is already-sanitized HTML (see
    notifications.forms.BroadcastForm) - safe to render directly into the email
    template, but SMS needs its own plain, budget-truncated derivation
    rather than raw markup.
    """
    if channel == ChannelEnum.EMAIL:
        html = render_to_string(
            "notifications/email/broadcast.html",
            {"broadcast": broadcast, "people": people, "family_name": broadcast.family.name},
        )
        body = html_to_plain_text(html)
        return _broadcast_subject(broadcast, people), body, html

    # html_to_plain_text handles both the entity-unescaping (a broadcast
    # author's "&" survives nh3-sanitized storage as "&amp;") and the
    # block-tag-to-newline conversion strip_tags alone doesn't do (see
    # that function's own docstring) - kept as real newlines here, not
    # flattened to one line: \n is part of the standard GSM-7 alphabet
    # (doesn't push the message into UCS-2 or cost extra budget), and a
    # real device renders it as an actual line break, so collapsing a
    # broadcast's paragraphs/list items into a run-on line was discarding
    # formatting the author actually wrote for no real technical reason.
    plain = html_to_plain_text(broadcast.text)
    text = render_to_string("notifications/sms/broadcast.txt", {"text": plain})
    text = _truncate_for_sms(text.strip())
    return "", text, ""


@shared_task(queue=TaskPriority.LOW)
def send_due_broadcasts() -> None:
    """Sends every due, unsent Broadcast.

    Runs every 5 minutes (see CELERY_BEAT_SCHEDULE) - a Broadcast is
    sent on its own send_at timestamp, not a daily batch like
    send_due_notifications, since "send immediately" (the default) or a
    specific scheduled time both need finer than day granularity.

    The update()-based claim below is what makes this safe to run
    concurrently (more than one worker, or an overlapping run because the
    previous one took over 5 minutes) and safe against a scheduled
    broadcast being edited or deleted right up until the moment it's due:
    only the run that actually flips is_sent 0->1 for a given row goes on
    to send it, and an update() affecting zero rows (already claimed, or
    the row's gone) is a normal, silent no-op rather than a race.
    """
    due_ids = list(
        Broadcast.objects.filter(is_sent=False, send_at__lte=timezone.now()).values_list("pk", flat=True)
    )
    logger.debug("send_due_broadcasts starting", due_count=len(due_ids))

    sent = 0
    for broadcast_id in due_ids:
        claimed = Broadcast.objects.filter(pk=broadcast_id, is_sent=False).update(
            is_sent=True, sent_at=timezone.now()
        )
        if not claimed:
            continue

        broadcast = Broadcast.objects.select_related("family", "created_by").get(pk=broadcast_id)
        people = list(broadcast.people.all())
        event_type = _broadcast_event_type(broadcast.family_id)
        audience = resolve_broadcast_audience(event_type=event_type, family=broadcast.family, people=people)

        rendered: dict[str, tuple[str, str, str]] = {}
        for account, channel, destination in audience:
            if channel not in rendered:
                rendered[channel] = _render_broadcast_message(broadcast, people, channel=channel)
            subject, body, html_body = rendered[channel]

            message = Message.objects.create(
                broadcast=broadcast,
                account=account,
                channel=channel,
                destination=destination,
                subject=subject,
                body=body,
                html_body=_personalize(html_body, destination=destination),
            )
            send_message.delay(message.pk)

        sent += 1

    logger.info("broadcasts sent", count=sent)
    metrics.beat_task_last_success_timestamp.labels(task_name="send_due_broadcasts").set(
        timezone.now().timestamp()
    )


def _metric_event_type(message: Message) -> str:
    """Resolves the bounded event_type label for the notifications_*_sent_total metrics.

    A raw EventType.code isn't safe to use directly - a family can set it
    to anything (see EventType.BuiltinCode's own docstring) - so this maps
    down to one of the 7 builtin codes, "custom" for any family-defined
    event type, or "broadcast" for a Message with no Occurrence at all.
    """
    if message.occurrence is None:
        return EventType.BuiltinCode.BROADCAST
    code = message.occurrence.event_type.code
    return code if code in EventType.BuiltinCode.values else "custom"


@shared_task(
    bind=True,
    queue=TaskPriority.NORMAL,
    autoretry_for=(Exception,),
    dont_autoretry_for=(SmsUnrecoverableError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_message(self: Task, message_id: int) -> None:
    """Sends one already-rendered Message, retrying transient failures with backoff.

    autoretry_for=(Exception,) covers both a generic provider failure and
    SmsRateLimitedError (notifications.sms) - the SNS global rate limit
    freeing up is exactly the kind of transient condition an exponential
    backoff is for, not a real failure. dont_autoretry_for excludes
    SmsUnrecoverableError, which retrying can never fix.

    acks_late/reject_on_worker_lost trade a possible duplicate send for
    the alternative being worse here: without them, a worker crash
    mid-task leaves the Message stuck at QUEUED forever with no retry -
    silently never notifying someone about the exact thing this app
    exists to notify them about. The duplicate-on-retry risk that trade
    would otherwise reopen is closed above (never re-raise once the
    provider call itself already succeeded).
    """
    message = Message.objects.get(pk=message_id)
    event_type = _metric_event_type(message)
    logger.debug(
        "send_message starting",
        message=message.uuid,
        channel=message.channel,
        event_type=event_type,
        attempt=self.request.retries + 1,
    )
    try:
        if message.channel == ChannelEnum.EMAIL:
            provider_response = send_email(
                to=message.destination,
                subject=message.subject,
                body=message.body,
                html=message.html_body or None,
                event_type=event_type,
                from_name=message.family.name,
                from_email=message.family.sender_email,
                reply_to=message.family.reply_to_email,
            )
        else:
            provider_response = send_sms(
                to=message.destination,
                body=message.body,
                event_type=event_type,
                sender_id=message.family.sms_sender_id,
            )
    except SmsUnrecoverableError as exc:
        message.status = Message.Status.FAILED
        message.error = str(exc)
        message.tries += 1
        message.save(update_fields=["status", "error", "tries"])
        logger.error(
            "message send failed permanently, not retrying",
            message=message.uuid,
            subject=message.subject,
            tries=message.tries,
            exc_info=exc,
        )
        raise
    except SmsRateLimitedError as exc:
        # Quiet unless this is the last attempt: autoretry_for's wrapper
        # gives up outside this function, so a burst that outlasts the
        # whole retry window would otherwise leave the Message stuck at
        # QUEUED forever with no record of why (see AGENTS.md).
        if self.request.retries >= self.max_retries:
            message.status = Message.Status.FAILED
            message.error = str(exc)
            message.tries += 1
            message.save(update_fields=["status", "error", "tries"])
            logger.error(
                "message send failed - sns rate limit never cleared within retry budget",
                message=message.uuid,
                subject=message.subject,
                tries=message.tries,
                exc_info=exc,
            )
            raise
        logger.debug(
            "message send deferred by sns rate limit",
            message=message.uuid,
            attempt=self.request.retries + 1,
            exc_info=exc,
        )
        raise
    except Exception as exc:
        message.status = Message.Status.FAILED
        message.error = str(exc)
        message.tries += 1
        message.save(update_fields=["status", "error", "tries"])
        logger.warning(
            "message send failed, will retry",
            message=message.uuid,
            subject=message.subject,
            tries=message.tries,
            attempt=self.request.retries + 1,
            exc_info=exc,
        )
        raise

    try:
        message.status = Message.Status.SENT
        message.sent_at = timezone.now()
        message.tries += 1
        message.provider_response = provider_response
        message.save(update_fields=["status", "sent_at", "tries", "provider_response"])
    except Exception as exc:
        # Never re-raise here - the send itself already succeeded, so a
        # retry from autoretry_for would send it again.
        logger.error(
            "message sent but failed to record - not retrying the send",
            message=message.uuid,
            subject=message.subject,
            channel=message.channel,
            exc_info=exc,
        )
        return

    logger.info(
        "message sent",
        message=message.uuid,
        subject=message.subject,
        channel=message.channel,
        status=message.status,
    )
