import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from freezegun import freeze_time
from hdate import HebrewDate
from hdate.hebrew_date import Months

from accounts.models import Account
from family.hebrew import gregorian_to_hebrew, resolve_send_date
from family.models import Person, Union
from notifications.models import Broadcast, EventType, Message, NotificationPreference, Occurrence
from notifications.tasks import (
    compute_occurrences,
    compute_occurrences_for_person,
    compute_occurrences_for_union,
    person_has_passed_coming_of_age,
    send_due_broadcasts,
    send_due_notifications,
)
from notifications.tests.conftest import member as _member
from tenants.models import Family, FamilyMembership

pytestmark = pytest.mark.django_db


def _future_anchor() -> tuple[int, int, int]:
    """A (hebrew_year, month, day) triple for tomorrow - guaranteed to be
    strictly ahead of today, in whatever Hebrew year that actually falls
    in. A hardcoded Tishrei 3 works fine on the day a test is written, but
    silently starts landing in the past for that year as real time moves
    through the calendar, since this year's occurrence for it is no
    longer upcoming. This used to search forward for a same-Hebrew-year
    date and raise if it couldn't find one, which broke outright on Elul
    29 (the one day per year where every remaining day already belongs to
    next year) - callers use the returned year instead of separately
    assuming "today's" Hebrew year, so the Rosh Hashanah boundary is just
    another valid case, not a special one."""
    tomorrow = timezone.localdate() + dt.timedelta(days=1)
    hd = gregorian_to_hebrew(tomorrow)
    return hd.year, hd.month.value, hd.day


def test_compute_occurrences_creates_rows_for_any_person_with_an_anchor_date(family):
    # A fixed early-in-the-year anchor, not _future_anchor(): the point
    # here is just "some occurrences get created with valid dates", not
    # pinning an exact count - how many of the 3 loop years are still
    # upcoming depends on where in the Hebrew year "today" happens to
    # fall, which isn't this test's concern.
    Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_gregorian=dt.date(1990, 9, 22),
        dob_hebrew_year=5751,
        dob_hebrew_month=1,
        dob_hebrew_day=3,
    )

    compute_occurrences()

    occurrences = Occurrence.objects.filter(event_type__code=EventType.BuiltinCode.BIRTHDAY)
    assert occurrences.exists()
    today = timezone.localdate()
    assert all(o.occurrence_date >= today for o in occurrences)
    assert all(o.occurrence_date is not None and o.send_date is not None for o in occurrences)


@pytest.mark.parametrize(
    ["dob_kwargs", "dod_days_ago", "event_code"],
    [
        [{}, None, EventType.BuiltinCode.YAHRZEIT],
        [
            {"dob_hebrew_year": 5751, "dob_hebrew_month": 1, "dob_hebrew_day": 3},
            1,
            EventType.BuiltinCode.BIRTHDAY,
        ],
        [{}, None, EventType.BuiltinCode.BIRTHDAY],
    ],
    ids=[
        "yahrzeit skipped for a living person",
        "birthday skipped for a deceased person",
        "birthday skipped for a person with no dob",
    ],
)
def test_compute_occurrences_skips_event_type(family, dob_kwargs, dod_days_ago, event_code):
    dod_kwargs = {}
    if dod_days_ago is not None:
        dod_kwargs["dod_gregorian"] = timezone.localdate() - dt.timedelta(days=dod_days_ago)
    Person.objects.create(
        family=family, first_name_en="Test", last_name_en="Person", **dob_kwargs, **dod_kwargs
    )

    compute_occurrences()

    assert Occurrence.objects.filter(event_type__code=event_code).count() == 0


def test_compute_occurrences_for_person_removes_future_birthdays_once_deceased(family):
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_hebrew_year=5751,
        dob_hebrew_month=1,
        dob_hebrew_day=3,
    )
    compute_occurrences_for_person(person)
    assert Occurrence.objects.filter(person=person, event_type__code=EventType.BuiltinCode.BIRTHDAY).exists()

    person.dod_gregorian = timezone.localdate() - dt.timedelta(days=1)
    person.save(update_fields=["dod_gregorian", "is_living"])
    compute_occurrences_for_person(person)

    assert not Occurrence.objects.filter(
        person=person, event_type__code=EventType.BuiltinCode.BIRTHDAY, is_sent=False
    ).exists()


def test_compute_occurrences_is_idempotent(family):
    Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_hebrew_year=5751,
        dob_hebrew_month=1,
        dob_hebrew_day=3,
    )

    compute_occurrences()
    count_after_first_run = Occurrence.objects.filter(event_type__code=EventType.BuiltinCode.BIRTHDAY).count()
    assert count_after_first_run > 0
    compute_occurrences()

    assert (
        Occurrence.objects.filter(event_type__code=EventType.BuiltinCode.BIRTHDAY).count()
        == count_after_first_run
    )


def test_send_due_notifications_notifies_family_members_by_default(family, birthday_event_type):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _member(family, email="test@example.com")

    occurrence = Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    send_due_notifications()

    occurrence.refresh_from_db()
    assert occurrence.is_sent is True

    message = Message.objects.get(occurrence=occurrence)
    assert message.destination == "test@example.com"
    assert message.status == Message.Status.SENT
    assert message.provider_response == {"sent_count": 1}


def test_send_due_notifications_does_not_query_parents_per_occurrence(family, birthday_event_type):
    # Person.parents_label (rendered into the email via _parents.html)
    # reads person.father/person.mother - without those chained into the
    # occurrence's own select_related, each occurrence's email render cost
    # 2 extra un-batched Person queries. Proven by asserting the same
    # query count for a person with no parents on file and one with both,
    # rather than an arbitrary cap.
    _member(family, email="test@example.com")

    # Warm up first - Django's Site framework (used by the email footer's
    # absolute URLs) caches its lookup after the first access, so an
    # uncontrolled first call would always cost one extra query regardless
    # of parents_label, muddying the comparison below.
    warmup = Person.objects.create(family=family, first_name_en="Warmup", last_name_en="Person")
    Occurrence.objects.create(
        person=warmup,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )
    send_due_notifications()

    childless = Person.objects.create(family=family, first_name_en="Solo", last_name_en="Person")
    Occurrence.objects.create(
        person=childless,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )
    with CaptureQueriesContext(connection) as no_parents:
        send_due_notifications()

    father = Person.objects.create(family=family, first_name_en="Dad", last_name_en="Person")
    mother = Person.objects.create(family=family, first_name_en="Mom", last_name_en="Person")
    with_parents = Person.objects.create(
        family=family, first_name_en="Kid", last_name_en="Person", father=father, mother=mother
    )
    Occurrence.objects.create(
        person=with_parents,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )
    with CaptureQueriesContext(connection) as with_parents_ctx:
        send_due_notifications()

    assert len(with_parents_ctx.captured_queries) == len(no_parents.captured_queries)

    message = Message.objects.get(occurrence__person=with_parents)
    assert "Dad &amp; Mom" in message.html_body or "Dad & Mom" in message.html_body


def test_send_due_notifications_skips_accounts_outside_the_family(family, birthday_event_type):
    other_family = Family.objects.create(name="Other Family")
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _member(other_family, email="outsider@example.com")

    Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    send_due_notifications()

    assert Message.objects.count() == 0


@pytest.mark.parametrize(
    ["mute_scoped_to_person"],
    [
        [True],
        [False],
    ],
    ids=[
        "respects a person-specific mute",
        "respects a whole-event-type mute",
    ],
)
def test_send_due_notifications_respects_a_mute(family, birthday_event_type, mute_scoped_to_person):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = _member(family)
    NotificationPreference.objects.create(
        account=account,
        event_type=birthday_event_type,
        person=person if mute_scoped_to_person else None,
        channel="email",
        state=NotificationPreference.State.MUTED,
    )

    Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    send_due_notifications()

    assert Message.objects.count() == 0


def test_send_due_notifications_respects_the_global_channel_toggle(family, birthday_event_type):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = _member(family)
    account.email_notifications_enabled = False
    account.save(update_fields=["email_notifications_enabled"])

    Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    send_due_notifications()

    assert Message.objects.count() == 0


def test_send_due_notifications_does_not_resend_already_sent_occurrences(family, birthday_event_type):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _member(family, email="test@example.com")

    Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
        is_sent=True,
    )

    send_due_notifications()

    assert Message.objects.count() == 0


def test_send_due_notifications_does_not_double_send_under_a_concurrent_claim(
    monkeypatch, family, birthday_event_type
):
    """Regression test: send_due_notifications used to set is_sent=True only
    after sending, not claim it atomically before sending (unlike its
    sibling send_due_broadcasts). Simulates a second worker's claim landing
    first for the same occurrence - this run's own claim (an update()
    affecting is_sent 0->1) must then affect zero rows and skip it instead
    of sending anyway."""
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _member(family, email="test@example.com")
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    real_filter = Occurrence.objects.filter

    def _filter_that_races(*args, **kwargs):
        if kwargs.get("pk") == occurrence.pk and kwargs.get("is_sent") is False:
            real_filter(pk=occurrence.pk, is_sent=False).update(is_sent=True)
        return real_filter(*args, **kwargs)

    monkeypatch.setattr(Occurrence.objects, "filter", _filter_that_races)

    send_due_notifications()

    assert Message.objects.count() == 0


def test_send_due_notifications_sends_an_overdue_occurrence(family, birthday_event_type):
    """Regression test: send_date used to be matched with send_date=today
    (an exact equality check), so an occurrence whose send_date had
    already landed in the past - which happens for real, see
    test_compute_occurrences_produces_a_past_send_date_when_the_anchor_
    falls_on_yom_tov below - could never be picked up by any later run,
    ever. send_date__lte catches up on it instead, the same self-healing
    reasoning as send_due_broadcasts' own send_at__lte."""
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _member(family, email="test@example.com")
    Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate() - dt.timedelta(days=1),
        send_date=timezone.localdate() - dt.timedelta(days=1),
    )

    send_due_notifications()

    assert Message.objects.count() == 1


def test_send_due_notifications_does_not_send_a_future_occurrence(family, birthday_event_type):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _member(family, email="test@example.com")
    Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate() + dt.timedelta(days=1),
        send_date=timezone.localdate() + dt.timedelta(days=1),
    )

    send_due_notifications()

    assert Message.objects.count() == 0


def test_send_due_notifications_uses_the_familys_sms_sender_id(family, birthday_event_type):
    family.sms_sender_id = "RokachFam"
    family.save(update_fields=["sms_sender_id"])
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = Account.objects.create_user(phone="+15551234567")
    FamilyMembership.objects.create(account=account, family=family, role=FamilyMembership.Role.MEMBER)
    Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    send_due_notifications()

    message = Message.objects.get(channel="sms")
    assert message.provider_response["sender_id"] == "RokachFam"


def test_send_due_notifications_falls_back_to_the_default_sms_sender_id_when_blank(
    family, birthday_event_type
):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    account = Account.objects.create_user(phone="+15551234567")
    FamilyMembership.objects.create(account=account, family=family, role=FamilyMembership.Role.MEMBER)
    Occurrence.objects.create(
        person=person,
        event_type=birthday_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    send_due_notifications()

    message = Message.objects.get(channel="sms")
    assert message.provider_response["sender_id"] == "FamilyTree"


def test_compute_occurrences_for_person_creates_rows_immediately(family):
    # This is what runs right after a person is added/edited, instead of
    # waiting for the nightly sweep - without it, a freshly added person
    # shows nothing anywhere until the next 2am run.
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_hebrew_year=5751,
        dob_hebrew_month=1,
        dob_hebrew_day=3,
    )

    compute_occurrences_for_person(person)

    assert Occurrence.objects.filter(person=person, event_type__code=EventType.BuiltinCode.BIRTHDAY).exists()


def test_compute_occurrences_for_union_creates_rows_immediately(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_hebrew_year=5770,
        marriage_hebrew_month=1,
        marriage_hebrew_day=1,
    )

    compute_occurrences_for_union(union)

    assert Occurrence.objects.filter(union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY).exists()


def test_compute_occurrences_for_union_removes_future_occurrences_once_divorced(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_hebrew_year=5770,
        marriage_hebrew_month=1,
        marriage_hebrew_day=1,
    )
    compute_occurrences_for_union(union)
    assert Occurrence.objects.filter(union=union).exists()

    union.status = Union.Status.DIVORCED
    union.save(update_fields=["status"])
    compute_occurrences_for_union(union)

    # Only future, not-yet-sent rows get cleaned up - this year's
    # anniversary anchor (Tishrei 1) has already passed, so that one row
    # is history, not schedule, and is deliberately left alone.
    assert not Occurrence.objects.filter(
        union=union, is_sent=False, send_date__gte=timezone.localdate()
    ).exists()


@pytest.mark.parametrize(
    ["gender", "years_ago", "expected_code"],
    [
        [Person.Gender.FEMALE, 12, EventType.BuiltinCode.BAT_MITZVAH],
        [Person.Gender.MALE, 13, EventType.BuiltinCode.BAR_MITZVAH],
    ],
    ids=[
        "bat mitzvah supersedes birthday at twelve",
        "bar mitzvah supersedes birthday at thirteen",
    ],
)
def test_compute_occurrences_supersedes_birthday_with_coming_of_age(family, gender, years_ago, expected_code):
    anchor_year, month, day = _future_anchor()
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        gender=gender,
        dob_hebrew_year=anchor_year - years_ago,
        dob_hebrew_month=month,
        dob_hebrew_day=day,
    )

    compute_occurrences_for_person(person)

    assert Occurrence.objects.filter(
        person=person, event_type__code=expected_code, hebrew_year=anchor_year
    ).exists()
    assert not Occurrence.objects.filter(
        person=person, event_type__code=EventType.BuiltinCode.BIRTHDAY, hebrew_year=anchor_year
    ).exists()
    # Other in-range years are still ordinary birthdays.
    assert (
        Occurrence.objects.filter(person=person, event_type__code=EventType.BuiltinCode.BIRTHDAY)
        .exclude(hebrew_year=anchor_year)
        .exists()
    )


def test_compute_occurrences_does_not_guess_coming_of_age_without_a_birth_year(family):
    # Only month/day known - the common case for imported data. Guessing
    # the Hebrew birth year from the Gregorian date isn't allowed here;
    # every year stays an ordinary birthday until someone enters it.
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Girl",
        gender=Person.Gender.FEMALE,
        dob_gregorian=dt.date(2013, 1, 1),
        dob_hebrew_month=1,
        dob_hebrew_day=3,
    )

    compute_occurrences_for_person(person)

    assert Occurrence.objects.filter(person=person, event_type__code=EventType.BuiltinCode.BIRTHDAY).exists()
    assert not Occurrence.objects.filter(
        person=person,
        event_type__code__in=[EventType.BuiltinCode.BAR_MITZVAH, EventType.BuiltinCode.BAT_MITZVAH],
    ).exists()


def test_compute_occurrences_relabels_an_existing_birthday_after_a_gender_correction(family):
    anchor_year, month, day = _future_anchor()
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Girl",
        dob_hebrew_year=anchor_year - 12,
        dob_hebrew_month=month,
        dob_hebrew_day=day,
    )
    # family.signals already computed a Birthday occurrence for this
    # exact (person, event_type, hebrew_year) the moment the person was
    # created above - that's the pre-correction state this test wants,
    # for free.
    assert Occurrence.objects.filter(
        person=person, event_type__code=EventType.BuiltinCode.BIRTHDAY, hebrew_year=anchor_year
    ).exists()

    person.gender = Person.Gender.FEMALE
    person.save(update_fields=["gender"])
    compute_occurrences_for_person(person)

    assert not Occurrence.objects.filter(
        person=person, event_type__code=EventType.BuiltinCode.BIRTHDAY, hebrew_year=anchor_year
    ).exists()
    assert Occurrence.objects.filter(
        person=person, event_type__code=EventType.BuiltinCode.BAT_MITZVAH, hebrew_year=anchor_year
    ).exists()


def test_compute_occurrences_for_union_does_not_fire_anniversary_before_the_wedding_year(family):
    # A union whose marriage_hebrew_year is still ahead of the current
    # Hebrew year is an upcoming wedding (see Union.is_upcoming) - it
    # shouldn't get an "anniversary" occurrence just because this year's
    # month/day happens to match the anchor.
    anchor_year, month, day = _future_anchor()
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_hebrew_year=anchor_year + 1,
        marriage_hebrew_month=month,
        marriage_hebrew_day=day,
    )

    compute_occurrences_for_union(union)

    anniversaries = Occurrence.objects.filter(union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY)
    assert anniversaries.exists()
    assert all(o.hebrew_year >= anchor_year + 1 for o in anniversaries)


def test_compute_occurrences_for_union_creates_a_one_time_wedding_reminder(family):
    anchor_year, month, day = _future_anchor()
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_hebrew_year=anchor_year,
        marriage_hebrew_month=month,
        marriage_hebrew_day=day,
    )

    compute_occurrences_for_union(union)

    wedding_occurrences = Occurrence.objects.filter(
        union=union, event_type__code=EventType.BuiltinCode.WEDDING
    )
    # recurs=False - exactly one row, for the actual wedding year, not one
    # per year in the horizon.
    assert wedding_occurrences.count() == 1
    occurrence = wedding_occurrences.get()
    assert occurrence.hebrew_year == anchor_year
    expected_send_date, _shifted = resolve_send_date(occurrence.occurrence_date - dt.timedelta(days=3))
    assert occurrence.send_date == expected_send_date


def test_compute_occurrences_for_union_skips_anniversary_once_a_spouse_has_died(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(
        family=family,
        first_name_en="B",
        last_name_en="Test",
        dod_gregorian=timezone.localdate() - dt.timedelta(days=1),
    )
    union = Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_hebrew_year=5770,
        marriage_hebrew_month=1,
        marriage_hebrew_day=1,
    )

    compute_occurrences_for_union(union)

    assert (
        Occurrence.objects.filter(union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY).count()
        == 0
    )


def test_compute_occurrences_for_union_skips_anniversary_when_a_spouse_is_untracked(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(
        family=family, first_name_en="B", last_name_en="Test", notifications_enabled=False
    )
    union = Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_hebrew_year=5770,
        marriage_hebrew_month=1,
        marriage_hebrew_day=1,
    )

    compute_occurrences_for_union(union)

    assert (
        Occurrence.objects.filter(union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY).count()
        == 0
    )


def test_compute_occurrences_for_union_removes_future_occurrences_once_a_spouse_becomes_untracked(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_hebrew_year=5770,
        marriage_hebrew_month=1,
        marriage_hebrew_day=1,
    )
    compute_occurrences_for_union(union)
    assert Occurrence.objects.filter(
        union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY, is_sent=False
    ).exists()

    person_b.notifications_enabled = False
    person_b.save(update_fields=["notifications_enabled"])
    compute_occurrences_for_union(union)

    assert not Occurrence.objects.filter(
        union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY, is_sent=False
    ).exists()


def test_compute_occurrences_for_union_does_not_delete_already_sent_history(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(person_a=person_a, person_b=person_b)
    anniversary = EventType.objects.get(family=None, code=EventType.BuiltinCode.ANNIVERSARY)
    sent_occurrence = Occurrence.objects.create(
        union=union,
        event_type=anniversary,
        hebrew_year=5784,
        occurrence_date=dt.date(2024, 1, 1),
        send_date=dt.date(2024, 1, 1),
        is_sent=True,
    )

    union.status = Union.Status.DIVORCED
    union.save(update_fields=["status"])
    compute_occurrences_for_union(union)

    assert Occurrence.objects.filter(pk=sent_occurrence.pk).exists()


def test_compute_occurrences_for_union_deletes_an_unsent_occurrence_even_with_a_past_send_date(family):
    """Regression test for a real bug: resolve_send_date walks a
    notification backward across Shabbat/Yom Tov (see family.hebrew), so
    an occurrence computed on its own anchor date, when that date is
    itself Yom Tov, gets a send_date already in the past the moment it's
    created - this actually happened, for a union anchored on 1 Tishrei
    recomputed on Rosh Hashanah itself (see the freezegun-based test
    below for the full end-to-end mechanism). _delete_stale_unsent_
    occurrences used to also filter on send_date__gte=today, which
    silently left a row like this one uncleanable forever, in addition to
    unsendable (see test_send_due_notifications_sends_an_overdue_
    occurrence). Built directly here, rather than depending on today
    actually being Rosh Hashanah, so it's reproducible on any day."""
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(person_a=person_a, person_b=person_b)
    anniversary = EventType.objects.get(family=None, code=EventType.BuiltinCode.ANNIVERSARY)
    stuck_occurrence = Occurrence.objects.create(
        union=union,
        event_type=anniversary,
        hebrew_year=5787,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate() - dt.timedelta(days=1),
        shifted_for_shabbat_or_yomtov=True,
    )

    person_b.notifications_enabled = False
    person_b.save(update_fields=["notifications_enabled"])
    compute_occurrences_for_union(union)

    assert not Occurrence.objects.filter(pk=stuck_occurrence.pk).exists()


def test_compute_occurrences_produces_a_past_send_date_when_the_anchor_falls_on_yom_tov(family):
    """End-to-end reproduction of the actual bug mechanism, frozen to a
    real Rosh Hashanah so it doesn't depend on when the suite happens to
    run: a union anchored on 1 Tishrei, recomputed on 1 Tishrei itself
    (Rosh Hashanah - a Yom Tov), gets an Anniversary occurrence whose
    send_date has already been shifted to the day before "today" by the
    Shabbat/Yom Tov walk-back in family.hebrew.resolve_send_date. Both
    send_due_notifications and _delete_stale_unsent_occurrences have to
    treat this row as "still due"/"still cleanable" despite that, rather
    than a plain send_date/today comparison quietly losing it forever -
    see the two regression tests this pairs with."""
    with freeze_time("2026-09-12"):  # 1 Tishrei 5787 - Rosh Hashanah
        assert gregorian_to_hebrew(timezone.localdate()) == HebrewDate(5787, Months.TISHREI, 1)

        person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
        person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
        union = Union.objects.create(
            person_a=person_a,
            person_b=person_b,
            marriage_hebrew_year=5770,
            marriage_hebrew_month=Months.TISHREI,
            marriage_hebrew_day=1,
        )

        compute_occurrences_for_union(union)

        occurrence = Occurrence.objects.get(
            union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY, hebrew_year=5787
        )
        assert occurrence.shifted_for_shabbat_or_yomtov is True
        assert occurrence.send_date < timezone.localdate()
        assert occurrence.is_sent is False


@pytest.mark.parametrize(
    ["gender", "dob_kind", "expected"],
    [
        [Person.Gender.MALE, "hebrew_40_years_ago", True],
        [Person.Gender.FEMALE, "gregorian_5_years_ago", False],
        [Person.Gender.MALE, "none", False],
    ],
    ids=[
        "prefers hebrew year over gregorian age - past the age",
        "falls back to gregorian age when hebrew year is unknown - not yet",
        "false with no birth data at all",
    ],
)
def test_person_has_passed_coming_of_age(family, gender, dob_kind, expected):
    dob_kwargs = {}
    if dob_kind == "hebrew_40_years_ago":
        today_hebrew_year = gregorian_to_hebrew(timezone.localdate()).year
        dob_kwargs["dob_hebrew_year"] = today_hebrew_year - 40
    elif dob_kind == "gregorian_5_years_ago":
        dob_kwargs["dob_gregorian"] = timezone.localdate() - dt.timedelta(days=int(5 * 365.25))
    person = Person.objects.create(
        family=family, first_name_en="Test", last_name_en="Person", gender=gender, **dob_kwargs
    )
    assert person_has_passed_coming_of_age(person) is expected


def test_send_due_broadcasts_sends_a_due_broadcast(family):
    account = _member(family, email="test@example.com")
    creator = _member(family, email="creator@example.com")
    broadcast = Broadcast.objects.create(
        family=family, text="Hello everyone", created_by=creator, send_at=timezone.now()
    )

    send_due_broadcasts()

    broadcast.refresh_from_db()
    assert broadcast.is_sent is True
    assert broadcast.sent_at is not None

    message = Message.objects.get(broadcast=broadcast, account=account)
    assert message.destination == "test@example.com"
    assert message.status == Message.Status.SENT
    assert "Hello everyone" in message.body


def test_send_due_broadcasts_skips_ones_scheduled_for_the_future(family):
    _member(family, email="test@example.com")
    creator = _member(family, email="creator@example.com")
    Broadcast.objects.create(
        family=family,
        text="Not yet",
        created_by=creator,
        send_at=timezone.now() + dt.timedelta(hours=1),
    )

    send_due_broadcasts()

    assert Message.objects.count() == 0


def test_send_due_broadcasts_does_not_resend_an_already_sent_broadcast(family):
    _member(family, email="test@example.com")
    creator = _member(family, email="creator@example.com")
    Broadcast.objects.create(
        family=family,
        text="Old news",
        created_by=creator,
        send_at=timezone.now() - dt.timedelta(days=1),
        is_sent=True,
        sent_at=timezone.now() - dt.timedelta(days=1),
    )

    send_due_broadcasts()

    assert Message.objects.count() == 0


def test_send_due_broadcasts_respects_a_whole_type_mute(family):
    # The only family member - a creator who isn't also a member (e.g. an
    # owner without their own Account.email_notifications_enabled off)
    # would otherwise still receive their own broadcast and mask this.
    account = _member(family, email="test@example.com")
    creator = Account.objects.create_user(email="creator@example.com")
    broadcast_event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BROADCAST)
    NotificationPreference.objects.create(
        account=account,
        event_type=broadcast_event_type,
        channel="email",
        state=NotificationPreference.State.MUTED,
    )
    Broadcast.objects.create(family=family, text="Hi", created_by=creator, send_at=timezone.now())

    send_due_broadcasts()

    assert Message.objects.count() == 0


def test_send_due_broadcasts_unions_audience_across_tied_people(family):
    account = _member(family, email="test@example.com")
    creator = _member(family, email="creator@example.com")
    broadcast_event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BROADCAST)
    NotificationPreference.objects.create(
        account=account,
        event_type=broadcast_event_type,
        channel="email",
        state=NotificationPreference.State.IMMEDIATE_FAMILY_ONLY,
    )
    viewer_person = Person.objects.create(family=family, first_name_en="Viewer", last_name_en="Person")
    viewer_person.account = account
    viewer_person.save(update_fields=["account"])
    cousin = Person.objects.create(family=family, first_name_en="Cousin", last_name_en="Person")
    sibling = Person.objects.create(family=family, first_name_en="Sib", last_name_en="Person")
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    viewer_person.father = parent
    viewer_person.save(update_fields=["father"])
    sibling.father = parent
    sibling.save(update_fields=["father"])

    broadcast = Broadcast.objects.create(family=family, text="Update", created_by=creator)
    broadcast.people.set([cousin, sibling])

    send_due_broadcasts()

    # Included because of the sibling, even though the cousin alone
    # wouldn't have qualified under immediate_family_only.
    assert Message.objects.filter(broadcast=broadcast, account=account).exists()


def test_send_due_broadcasts_excludes_accounts_outside_the_family(family):
    other_family = Family.objects.create(name="Other Family")
    _member(other_family, email="outsider@example.com")
    # Not a member of `family` either - only outsider@example.com exists
    # as a family membership anywhere, and it's the wrong family.
    creator = Account.objects.create_user(email="creator@example.com")
    Broadcast.objects.create(family=family, text="Hi", created_by=creator)

    send_due_broadcasts()

    assert Message.objects.count() == 0
