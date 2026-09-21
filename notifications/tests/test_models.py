import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Account
from family.models import Person, Union
from notifications.enums import ShiftReason
from notifications.models import Broadcast, EventType, Message, NotificationPreference, Occurrence
from tenants.models import Family

pytestmark = pytest.mark.django_db


def test_global_event_types_are_shared_across_families():
    family_a = Family.objects.create(name="A")
    family_b = Family.objects.create(name="B")

    birthday_for_a = EventType.objects.filter(code=EventType.BuiltinCode.BIRTHDAY).first()
    birthday_for_b = EventType.objects.filter(code=EventType.BuiltinCode.BIRTHDAY).first()

    assert birthday_for_a == birthday_for_b
    assert birthday_for_a.family is None
    assert family_a != family_b


def _broadcast_event_type():
    return EventType.objects.get(family=None, code=EventType.BuiltinCode.BROADCAST)


# --- A Broadcast has no per-recipient targeting - see AGENTS.md and
# Broadcast's own docstring. NotificationPreference.clean() is the
# model-level guardrail for that (notifications.views.
# TogglePersonPreferenceView is the same rule enforced against a raw
# .create() call, tested in notifications/tests/test_views.py). ---


def test_broadcast_mute_allows_a_whole_type_mute(family):
    account = Account.objects.create_user(email="a@example.com")
    preference = NotificationPreference(
        account=account,
        event_type=_broadcast_event_type(),
        channel="email",
        state=NotificationPreference.State.MUTED,
    )
    preference.full_clean()  # does not raise


def test_broadcast_mute_rejects_a_person_specific_override(family):
    account = Account.objects.create_user(email="a@example.com")
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    preference = NotificationPreference(
        account=account,
        event_type=_broadcast_event_type(),
        person=person,
        channel="email",
        state=NotificationPreference.State.MUTED,
    )
    with pytest.raises(ValidationError):
        preference.full_clean()


# --- EventType.allowed_states gates which NotificationState values make
# sense for a given event type - checked both on a NotificationPreference
# row (clean()) and on the EventType's own default_state (clean()). See
# AGENTS.md and EventType.allowed_states' own docstring. ---


def _anniversary_event_type():
    return EventType.objects.get(family=None, code=EventType.BuiltinCode.ANNIVERSARY)


def _yahrzeit_event_type():
    return EventType.objects.get(family=None, code=EventType.BuiltinCode.YAHRZEIT)


def test_preference_rejects_ancestors_only_for_a_union_anchored_type(family):
    account = Account.objects.create_user(email="a@example.com")
    preference = NotificationPreference(
        account=account,
        event_type=_anniversary_event_type(),
        channel="email",
        state=NotificationPreference.State.ANCESTORS_ONLY,
    )
    with pytest.raises(ValidationError):
        preference.full_clean()


def test_preference_allows_ancestors_only_for_yahrzeit(family):
    account = Account.objects.create_user(email="a@example.com")
    preference = NotificationPreference(
        account=account,
        event_type=_yahrzeit_event_type(),
        channel="email",
        state=NotificationPreference.State.ANCESTORS_ONLY,
    )
    preference.full_clean()  # does not raise


def test_event_type_clean_rejects_a_default_state_outside_allowed_states(family):
    event_type = EventType(
        family=family,
        code="anniversary-test",
        name="Test Anniversary",
        anchor=EventType.Anchor.MARRIAGE,
        applies_to_union=True,
        default_state=NotificationPreference.State.ANCESTORS_ONLY,
    )
    with pytest.raises(ValidationError):
        event_type.full_clean()


def test_event_type_clean_allows_a_default_state_within_allowed_states(family):
    event_type = EventType(
        family=family,
        code="anniversary-test",
        name="Test Anniversary",
        anchor=EventType.Anchor.MARRIAGE,
        applies_to_union=True,
        default_state=NotificationPreference.State.IMMEDIATE_FAMILY_ONLY,
    )
    event_type.full_clean()  # does not raise


# --- Broadcast.save() sanitizes unconditionally (nh3), not just the
# form - see notifications.models.BROADCAST_ALLOWED_TAGS. Trix's own
# default toolbar can't produce a <script> or an onerror attribute in
# the first place, but the model doesn't trust that - see its own
# docstring for why this is enforced here rather than only in
# notifications.forms.BroadcastForm. ---


def test_broadcast_sanitization_strips_a_script_tag(family):
    account = Account.objects.create_user(email="a@example.com")
    broadcast = Broadcast.objects.create(
        family=family, text="<script>alert(1)</script><div>Hi</div>", created_by=account
    )

    assert "<script" not in broadcast.text
    assert "<div>Hi</div>" in broadcast.text


def test_broadcast_sanitization_strips_an_event_handler_attribute(family):
    account = Account.objects.create_user(email="a@example.com")
    broadcast = Broadcast.objects.create(
        family=family, text='<div onclick="evil()">Hi</div>', created_by=account
    )

    assert "onclick" not in broadcast.text


def test_broadcast_sanitization_keeps_allowed_formatting_tags(family):
    account = Account.objects.create_user(email="a@example.com")
    html = '<div>Mazel tov <strong>Sari</strong>! <a href="https://example.com">details</a></div>'
    broadcast = Broadcast.objects.create(family=family, text=html, created_by=account)

    assert "<strong>Sari</strong>" in broadcast.text
    assert 'href="https://example.com"' in broadcast.text


def test_broadcast_sanitization_adds_a_safe_rel_to_links(family):
    account = Account.objects.create_user(email="a@example.com")
    broadcast = Broadcast.objects.create(
        family=family, text='<a href="https://example.com">link</a>', created_by=account
    )

    assert 'rel="noopener noreferrer"' in broadcast.text


def test_broadcast_sanitization_strips_an_image_tag(family):
    # Trix's own toolbar can produce one via drag/drop file attachment -
    # disabled client-side (notifications/static/notifications/js/
    # broadcast_editor.js) but not something the model should trust
    # regardless.
    account = Account.objects.create_user(email="a@example.com")
    broadcast = Broadcast.objects.create(
        family=family, text='<img src="https://example.com/x.png">Hi', created_by=account
    )

    assert "<img" not in broadcast.text
    assert "Hi" in broadcast.text


def test_occurrence_rejects_neither_person_nor_union(family):
    birthday = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)

    with pytest.raises(IntegrityError), transaction.atomic():
        Occurrence.objects.create(
            person=None,
            union=None,
            event_type=birthday,
            hebrew_year=5786,
            occurrence_date=timezone.localdate(),
            send_date=timezone.localdate(),
        )


def test_occurrence_rejects_both_person_and_union(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    spouse = Person.objects.create(family=family, first_name_en="Spouse", last_name_en="Person")
    union = Union.objects.create(person_a=person, person_b=spouse)
    birthday = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)

    with pytest.raises(IntegrityError), transaction.atomic():
        Occurrence.objects.create(
            person=person,
            union=union,
            event_type=birthday,
            hebrew_year=5786,
            occurrence_date=timezone.localdate(),
            send_date=timezone.localdate(),
        )


def test_occurrence_shift_reasons_stores_stable_values_not_labels(family):
    # ShiftReason.value (persisted here) is deliberately not the same
    # string as .label (only ever shown to a user) - see AGENTS.md's
    # "Enums" section. shift_reason_labels is the one property that maps
    # the stored stable values back to display text.
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    birthday = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=birthday,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
        shift_reasons=[ShiftReason.SHABBOS, ShiftReason.YOM_TOV],
    )

    occurrence.refresh_from_db()

    assert occurrence.shift_reasons == ["shabbos", "yom_tov"]
    assert occurrence.shift_reason_labels == ["Shabbos", "Yom Tov"]
    assert occurrence.shifted_for_shabbat_or_yomtov is True


def test_message_rejects_neither_occurrence_nor_broadcast(family):
    with pytest.raises(IntegrityError), transaction.atomic():
        Message.objects.create(
            occurrence=None, broadcast=None, channel="email", destination="a@example.com", body="x"
        )


def test_message_rejects_both_occurrence_and_broadcast(family):
    account = Account.objects.create_user(email="a@example.com")
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    birthday = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=birthday,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )
    broadcast = Broadcast.objects.create(family=family, text="Hi", created_by=account)

    with pytest.raises(IntegrityError), transaction.atomic():
        Message.objects.create(
            occurrence=occurrence,
            broadcast=broadcast,
            channel="email",
            destination="a@example.com",
            body="x",
        )


def test_message_family_resolves_from_a_person_occurrence(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    birthday = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    occurrence = Occurrence.objects.create(
        person=person,
        event_type=birthday,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )
    message = Message(occurrence=occurrence, channel="email", destination="a@example.com", body="x")

    assert message.family == family


def test_message_family_resolves_from_a_union_occurrence(family):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(person_a=person_a, person_b=person_b, status=Union.Status.MARRIED)
    anniversary = EventType.objects.get(family=None, code=EventType.BuiltinCode.ANNIVERSARY)
    occurrence = Occurrence.objects.create(
        union=union,
        event_type=anniversary,
        hebrew_year=5786,
        occurrence_date=timezone.localdate(),
        send_date=timezone.localdate(),
    )
    message = Message(occurrence=occurrence, channel="email", destination="a@example.com", body="x")

    assert message.family == family


def test_message_family_resolves_from_a_broadcast(family):
    account = Account.objects.create_user(email="a@example.com")
    broadcast = Broadcast.objects.create(family=family, text="Hi", created_by=account)
    message = Message(broadcast=broadcast, channel="sms", destination="+15551234567", body="Hi")

    assert message.family == family
