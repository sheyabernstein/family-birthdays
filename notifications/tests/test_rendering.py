import datetime as dt

import css_inline
import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone

import notifications.services
from accounts.models import Account
from family.models import Person, Union
from notifications.models import Broadcast, Channel, EventType, Occurrence
from notifications.services import absolute_url, send_email, send_sms, static_data_uri
from notifications.tasks import (
    SMS_CHAR_BUDGET,
    _render_broadcast_message,
    _render_occurrence_message,
    _truncate_for_sms,
)

pytestmark = pytest.mark.django_db


def _occurrence_for(family, code, *, days_ago=0):
    event_type = EventType.objects.get(family=None, code=code)
    person = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    occurrence_date = timezone.localdate() - dt.timedelta(days=days_ago)
    return Occurrence.objects.create(
        person=person,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=occurrence_date,
        send_date=occurrence_date,
    )


def _union_occurrence_for(family, code, *, days_ago=0):
    event_type = EventType.objects.get(family=None, code=code)
    person_a = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    person_b = Person.objects.create(family=family, first_name_en="Moshe", last_name_en="Rokach")
    union = Union.objects.create(person_a=person_a, person_b=person_b)
    occurrence_date = timezone.localdate() - dt.timedelta(days=days_ago)
    return Occurrence.objects.create(
        union=union,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=occurrence_date,
        send_date=occurrence_date,
    )


# --- Occurrence email rendering ---


@pytest.mark.parametrize(
    ["code"],
    [
        [EventType.BuiltinCode.BIRTHDAY],
        [EventType.BuiltinCode.YAHRZEIT],
        [EventType.BuiltinCode.BAR_MITZVAH],
        [EventType.BuiltinCode.BAT_MITZVAH],
    ],
    ids=["birthday", "yahrzeit", "bar mitzvah", "bat mitzvah"],
)
def test_occurrence_email_renders_the_persons_own_template_with_an_icon(family, code):
    occurrence = _occurrence_for(family, code)

    subject, body, html = _render_occurrence_message(occurrence, channel=Channel.EMAIL)

    assert "Sari Rokach" in subject
    assert "Sari Rokach" in html
    assert "Sari Rokach" in body
    assert "<script" not in html
    assert "data:image/png;base64," in html
    # Not a hardcoded "localhost:8000" - that only ever matched by
    # coincidence with settings.SITE_BASE_URL's own default, and broke
    # the moment a real .env set a different value (127.0.0.1 vs
    # localhost) with no code change at all.
    assert f'href="{settings.SITE_BASE_URL}/notifications/"' in html


@pytest.mark.parametrize(
    ["code"],
    [[EventType.BuiltinCode.ANNIVERSARY], [EventType.BuiltinCode.WEDDING]],
    ids=["anniversary", "wedding"],
)
def test_occurrence_email_renders_a_union_occurrence_with_both_spouses(family, code):
    occurrence = _union_occurrence_for(family, code)

    _subject, body, html = _render_occurrence_message(occurrence, channel=Channel.EMAIL)

    assert "Sari Rokach" in html and "Moshe Rokach" in html
    assert "Sari Rokach" in body and "Moshe Rokach" in body


def test_occurrence_email_shows_the_subjects_hebrew_first_name(family):
    event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    person = Person.objects.create(
        family=family, first_name_en="Blimi", last_name_en="Rokach", first_name_he="בלומא"
    )
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    _subject, _body, html = _render_occurrence_message(occurrence, channel=Channel.EMAIL)

    assert "(בלומא)" in html


def test_occurrence_email_shows_the_parents_label_even_without_a_naming_collision(family):
    event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    father = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach", father=father)
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    _subject, _body, html = _render_occurrence_message(occurrence, channel=Channel.EMAIL)

    assert "Shloime&#x27;s Blimi" in html or "Shloime's Blimi" in html


def test_occurrence_email_omits_the_parents_label_without_a_living_tracked_parent(family):
    event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    _subject, _body, html = _render_occurrence_message(occurrence, channel=Channel.EMAIL)

    assert "'s Blimi" not in html


def test_occurrence_email_falls_back_to_the_default_template_for_a_familys_own_custom_event_type(family):
    custom = EventType.objects.create(family=family, code="graduation", name="Graduation")
    occurrence = Occurrence.objects.create(
        person=Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach"),
        event_type=custom,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    _subject, body, html = _render_occurrence_message(occurrence, channel=Channel.EMAIL)

    assert "Graduation" in html
    assert "Sari Rokach" in body


def test_occurrence_email_plain_text_body_has_no_html_tags(family):
    occurrence = _occurrence_for(family, EventType.BuiltinCode.BIRTHDAY)

    _subject, body, _html = _render_occurrence_message(occurrence, channel=Channel.EMAIL)

    assert "<" not in body


# --- Occurrence SMS rendering ---


@pytest.mark.parametrize(
    ["code"],
    [
        [EventType.BuiltinCode.BIRTHDAY],
        [EventType.BuiltinCode.YAHRZEIT],
        [EventType.BuiltinCode.BAR_MITZVAH],
        [EventType.BuiltinCode.BAT_MITZVAH],
    ],
    ids=["birthday", "yahrzeit", "bar mitzvah", "bat mitzvah"],
)
def test_occurrence_sms_renders_the_persons_own_short_plain_text(family, code):
    occurrence = _occurrence_for(family, code)

    subject, body, html = _render_occurrence_message(occurrence, channel=Channel.SMS)

    assert subject == ""
    assert html == ""
    assert "Sari Rokach" in body
    assert "<" not in body
    assert len(body) <= SMS_CHAR_BUDGET


@pytest.mark.parametrize(
    ["code"],
    [[EventType.BuiltinCode.ANNIVERSARY], [EventType.BuiltinCode.WEDDING]],
    ids=["anniversary", "wedding"],
)
def test_occurrence_sms_renders_a_union_occurrence_with_both_spouses(family, code):
    occurrence = _union_occurrence_for(family, code)

    subject, body, html = _render_occurrence_message(occurrence, channel=Channel.SMS)

    assert subject == ""
    assert html == ""
    assert "Sari Rokach" in body and "Moshe Rokach" in body
    assert "<" not in body
    assert len(body) <= SMS_CHAR_BUDGET


def test_occurrence_sms_shows_the_subjects_hebrew_first_name(family):
    event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    person = Person.objects.create(
        family=family, first_name_en="Blimi", last_name_en="Rokach", first_name_he="בלומא"
    )
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    _subject, body, _html = _render_occurrence_message(occurrence, channel=Channel.SMS)

    assert "(בלומא)" in body


def test_occurrence_sms_shows_the_parents_label_even_without_a_naming_collision(family):
    # Same feature as the email templates (see the email-side test of the
    # same name) - this was implemented for email only and missed for
    # SMS entirely until caught here.
    event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    father = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach", father=father)
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    _subject, body, _html = _render_occurrence_message(occurrence, channel=Channel.SMS)

    assert "Shloime's Blimi" in body


def test_occurrence_sms_does_not_html_escape_an_ampersand_in_the_parents_label(family):
    # Regression: Django's normal template autoescaping was still active
    # for these plain-text .txt templates, so parents_label's own " & "
    # (used whenever both parents are shown) rendered as the literal
    # text "&amp;" in the actual SMS - never actually decoded back for a
    # channel that's read as plain text, not HTML.
    event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    father = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    mother = Person.objects.create(family=family, first_name_en="Bruchele", last_name_en="Rokach")
    person = Person.objects.create(
        family=family, first_name_en="Blimi", last_name_en="Rokach", father=father, mother=mother
    )
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    _subject, body, _html = _render_occurrence_message(occurrence, channel=Channel.SMS)

    assert "Shloime & Bruchele's Blimi" in body
    assert "&amp;" not in body
    assert "&#x27;" not in body


def test_occurrence_sms_falls_back_to_the_default_template(family):
    custom = EventType.objects.create(family=family, code="graduation", name="Graduation")
    occurrence = Occurrence.objects.create(
        person=Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach"),
        event_type=custom,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )

    _subject, body, _html = _render_occurrence_message(occurrence, channel=Channel.SMS)

    assert "Graduation" in body


# --- Regression coverage for the wording half of the send_date__lte fix
# (notifications.tasks.send_due_notifications) - a notification caught up
# late (occurrence_date already in the past by the time it's actually
# sent, e.g. after a worker outage) must not still claim "Today is ..."
# for an event that already happened. is_late is False whenever
# occurrence_date == today - including the normal case where send_date
# lands *earlier* than occurrence_date for a Shabbat/Yom Tov shift -
# since the event itself hasn't passed. ---


@pytest.mark.parametrize(
    ["code", "today_phrase", "late_phrase"],
    [
        [EventType.BuiltinCode.BIRTHDAY, "Today is", "birthday was on"],
        [EventType.BuiltinCode.YAHRZEIT, "Today is", "yahrzeit was on"],
        [EventType.BuiltinCode.BAR_MITZVAH, "Today is", "Bar Mitzvah was on"],
        [EventType.BuiltinCode.BAT_MITZVAH, "Today is", "Bat Mitzvah was on"],
    ],
    ids=["birthday", "yahrzeit", "bar mitzvah", "bat mitzvah"],
)
def test_person_occurrence_email_wording_by_lateness(family, code, today_phrase, late_phrase):
    on_time = _occurrence_for(family, code, days_ago=0)
    _subject, _body, html = _render_occurrence_message(on_time, channel=Channel.EMAIL)
    assert today_phrase in html
    assert late_phrase not in html

    late = _occurrence_for(family, code, days_ago=3)
    _subject, _body, html = _render_occurrence_message(late, channel=Channel.EMAIL)
    assert late_phrase in html
    assert today_phrase not in html


@pytest.mark.parametrize(
    ["code", "on_time_phrase", "late_phrase"],
    [
        [EventType.BuiltinCode.ANNIVERSARY, "Today is", "anniversary was on"],
        [EventType.BuiltinCode.WEDDING, "is coming up", "took place on"],
    ],
    ids=["anniversary", "wedding"],
)
def test_union_occurrence_email_wording_by_lateness(family, code, on_time_phrase, late_phrase):
    on_time = _union_occurrence_for(family, code, days_ago=0)
    _subject, _body, html = _render_occurrence_message(on_time, channel=Channel.EMAIL)
    assert on_time_phrase in html
    assert late_phrase not in html

    late = _union_occurrence_for(family, code, days_ago=3)
    _subject, _body, html = _render_occurrence_message(late, channel=Channel.EMAIL)
    assert late_phrase in html
    assert on_time_phrase not in html


def test_default_template_wording_by_lateness_for_a_person(family):
    custom = EventType.objects.create(family=family, code="graduation", name="Graduation")
    person = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")

    on_time = Occurrence.objects.create(
        person=person,
        event_type=custom,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )
    _subject, _body, html = _render_occurrence_message(on_time, channel=Channel.EMAIL)
    assert "Today is" in html

    late = Occurrence.objects.create(
        person=person,
        event_type=custom,
        hebrew_year=5787,
        occurrence_date=timezone.localdate() - dt.timedelta(days=3),
        send_date=timezone.localdate() - dt.timedelta(days=3),
    )
    _subject, _body, html = _render_occurrence_message(late, channel=Channel.EMAIL)
    assert "was on" in html
    assert "Today is" not in html


def test_sms_wording_switches_for_a_late_birthday(family):
    late = _occurrence_for(family, EventType.BuiltinCode.BIRTHDAY, days_ago=2)

    _subject, body, _html = _render_occurrence_message(late, channel=Channel.SMS)

    assert "was on" in body
    assert "is today" not in body


def test_sms_still_says_today_when_not_late(family):
    on_time = _occurrence_for(family, EventType.BuiltinCode.BIRTHDAY, days_ago=0)

    _subject, body, _html = _render_occurrence_message(on_time, channel=Channel.SMS)

    assert "is today" in body
    assert "was on" not in body


def test_shabbat_shift_ahead_of_occurrence_date_is_not_treated_as_late(family):
    # send_date earlier than occurrence_date (the normal Shabbat/Yom Tov
    # shift - see family.hebrew.resolve_send_date) is not "lateness" -
    # the event itself is still ahead, only the notification went out a
    # day early.
    event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    person = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate() + dt.timedelta(days=1),
        send_date=timezone.localdate(),
        shifted_for_shabbat_or_yomtov=True,
    )

    _subject, _body, html = _render_occurrence_message(occurrence, channel=Channel.EMAIL)

    assert "Today is" in html
    assert "was on" not in html


def test_subject_line_matches_the_body_wording_when_late(family):
    # A subject saying "today" over a body that says "was on <date>"
    # would be a confusing mismatch - see _occurrence_subject.
    late = _occurrence_for(family, EventType.BuiltinCode.BIRTHDAY, days_ago=3)

    subject, _body, _html = _render_occurrence_message(late, channel=Channel.EMAIL)

    assert "today" not in subject
    assert "was on" in subject


def test_subject_line_says_today_when_not_late(family):
    on_time = _occurrence_for(family, EventType.BuiltinCode.BIRTHDAY, days_ago=0)

    subject, _body, _html = _render_occurrence_message(on_time, channel=Channel.EMAIL)

    assert "today" in subject


def test_wedding_subject_says_coming_up_not_today_even_on_time(family):
    # Wedding is sent notify_days_before=3 ahead of the day itself, so
    # "today" was never accurate for it even in the on-time case.
    on_time = _union_occurrence_for(family, EventType.BuiltinCode.WEDDING, days_ago=0)

    subject, _body, _html = _render_occurrence_message(on_time, channel=Channel.EMAIL)

    assert "coming up" in subject
    assert "today" not in subject


def test_wedding_subject_says_was_on_when_late(family):
    late = _union_occurrence_for(family, EventType.BuiltinCode.WEDDING, days_ago=3)

    subject, _body, _html = _render_occurrence_message(late, channel=Channel.EMAIL)

    assert "was on" in subject
    assert "coming up" not in subject


# --- Broadcast rendering ---


def test_broadcast_email_includes_sanitized_text_and_tied_people(family):
    owner = Account.objects.create_user(email="owner@example.com")
    person = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    broadcast = Broadcast.objects.create(
        family=family, text="<div>Big <strong>news</strong>!</div>", created_by=owner
    )

    subject, body, html = _render_broadcast_message(broadcast, [person], channel=Channel.EMAIL)

    assert "<strong>news</strong>" in html
    assert "Sari Rokach" in html
    assert "Big news!" in body
    assert "Sari Rokach" in subject


def test_broadcast_email_without_tied_people_uses_a_generic_subject(family):
    owner = Account.objects.create_user(email="owner@example.com")
    broadcast = Broadcast.objects.create(family=family, text="Hello", created_by=owner)

    subject, _body, _html = _render_broadcast_message(broadcast, [], channel=Channel.EMAIL)

    assert subject == f"{family.name} update"


def test_broadcast_sms_strips_html_and_stays_within_budget(family):
    owner = Account.objects.create_user(email="owner@example.com")
    long_text = "<div>" + ("word " * 60) + "</div>"
    broadcast = Broadcast.objects.create(family=family, text=long_text, created_by=owner)

    subject, body, html = _render_broadcast_message(broadcast, [], channel=Channel.SMS)

    assert subject == ""
    assert html == ""
    assert "<" not in body
    assert len(body) <= SMS_CHAR_BUDGET
    assert body.endswith("…")


def test_broadcast_sms_does_not_html_escape_the_authors_own_text(family):
    # Regression: Broadcast.save() sanitizes text through nh3, which
    # re-serializes it as valid HTML - an author's own literal "&"
    # survives that round-trip as the entity "&amp;", which strip_tags()
    # alone (used to derive the plain SMS body) never decodes back.
    owner = Account.objects.create_user(email="owner@example.com")
    broadcast = Broadcast.objects.create(family=family, text="Mazel Tov to John & Jane!", created_by=owner)

    _subject, body, _html = _render_broadcast_message(broadcast, [], channel=Channel.SMS)

    assert "John & Jane" in body
    assert "&amp;" not in body


# --- _truncate_for_sms ---


def test_truncate_for_sms_leaves_short_text_untouched():
    assert _truncate_for_sms("short") == "short"


def test_truncate_for_sms_truncates_long_text_with_an_ellipsis():
    result = _truncate_for_sms("x" * 200)

    assert len(result) == SMS_CHAR_BUDGET
    assert result.endswith("…")


def test_truncate_for_sms_exact_budget_is_not_truncated():
    text = "x" * SMS_CHAR_BUDGET
    assert _truncate_for_sms(text) == text


def test_static_data_uri_embeds_the_asset_as_base64():
    uri = static_data_uri("notifications/img/event-icons/birth.png")

    assert uri.startswith("data:image/png;base64,")


def test_static_data_uri_raises_for_a_missing_asset():
    with pytest.raises(FileNotFoundError):
        static_data_uri("notifications/img/event-icons/does-not-exist.png")


def test_static_data_uri_is_memoized(monkeypatch):
    calls = []
    real_find = notifications.services.find_static

    def _counting_find(path):
        calls.append(path)
        return real_find(path)

    monkeypatch.setattr("notifications.services.find_static", _counting_find)
    notifications.services.static_data_uri.cache_clear()

    notifications.services.static_data_uri("notifications/img/event-icons/death.png")
    notifications.services.static_data_uri("notifications/img/event-icons/death.png")

    assert calls == ["notifications/img/event-icons/death.png"]


def test_absolute_url_builds_a_full_url_to_a_named_view():
    url = absolute_url("notifications:subscriptions")

    assert url.startswith(("http://", "https://"))
    assert url.endswith("/notifications/")


# --- send_email ---


def test_send_email_sends_a_real_multipart_email_when_html_is_given():
    send_email(to="a@example.com", subject="Subj", body="Plain body", html="<p>Rich</p>")

    sent = mail.outbox[-1]
    assert sent.body == "Plain body"
    assert len(sent.alternatives) == 1
    html_content, mimetype = sent.alternatives[0]
    # Run through css_inline (see notifications.services._inline_css),
    # which normalizes a bare fragment into a full document - the actual
    # content survives, just no longer byte-for-byte identical.
    assert "<p>Rich</p>" in html_content
    assert mimetype == "text/html"


def test_send_email_inlines_plain_css_rules_onto_their_elements():
    html = "<html><head><style>p { color: red; }</style></head><body><p>Hi</p></body></html>"

    send_email(to="a@example.com", subject="Subj", body="Plain body", html=html)

    html_content, _ = mail.outbox[-1].alternatives[0]
    assert 'style="color: red' in html_content


def test_send_email_keeps_at_rules_for_clients_that_support_them():
    html = (
        "<html><head><style>@media (prefers-color-scheme: dark) "
        "{ p { color: white; } }</style></head><body><p>Hi</p></body></html>"
    )

    send_email(to="a@example.com", subject="Subj", body="Plain body", html=html)

    html_content, _ = mail.outbox[-1].alternatives[0]
    assert "prefers-color-scheme: dark" in html_content


def test_send_email_falls_back_to_the_original_html_on_an_inlining_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise css_inline.InlineError("boom")

    monkeypatch.setattr("notifications.services.css_inline.inline", _raise)

    send_email(to="a@example.com", subject="Subj", body="Plain body", html="<p>Rich</p>")

    html_content, _ = mail.outbox[-1].alternatives[0]
    assert html_content == "<p>Rich</p>"


def test_send_email_is_plain_text_only_without_html():
    send_email(to="a@example.com", subject="Subj", body="Plain body")

    assert mail.outbox[-1].alternatives == []


def test_send_email_uses_the_default_sender_when_blank():
    send_email(to="a@example.com", subject="Subj", body="Plain body")

    assert mail.outbox[-1].from_email == f"Family Tree <{settings.DEFAULT_FROM_EMAIL}>"


def test_send_email_uses_the_given_sender_name_and_address():
    send_email(
        to="a@example.com",
        subject="Subj",
        body="Plain body",
        from_name="Family Rokach",
        from_email="noreply-rokach@family-tree.example",
    )

    assert mail.outbox[-1].from_email == "Family Rokach <noreply-rokach@family-tree.example>"


def test_send_email_omits_reply_to_when_blank():
    send_email(to="a@example.com", subject="Subj", body="Plain body")

    assert mail.outbox[-1].reply_to == []


def test_send_email_sets_reply_to_when_given():
    send_email(to="a@example.com", subject="Subj", body="Plain body", reply_to="owner@example.com")

    assert mail.outbox[-1].reply_to == ["owner@example.com"]


# --- send_sms ---


def test_send_sms_uses_the_default_sender_id_when_blank():
    result = send_sms(to="+15551234567", body="Hi")

    assert result["sender_id"] == "FamilyTree"


def test_send_sms_uses_the_given_sender_id():
    result = send_sms(to="+15551234567", body="Hi", sender_id="RokachFam")

    assert result["sender_id"] == "RokachFam"
