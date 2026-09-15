import datetime as dt

import pytest
from django.utils import timezone

from accounts.models import Account
from family.models import Person, Union
from notifications.models import Broadcast, EventType, NotificationPreference
from tenants.models import FamilyMembership

pytestmark = pytest.mark.django_db


def _member(family, role):
    account = Account.objects.create_user(email=f"{role}@example.com")
    FamilyMembership.objects.create(account=account, family=family, role=role)
    return account


def _login_as(client, account, family):
    client.force_login(account)
    session = client.session
    session["family_id"] = family.id
    session.save()


def test_staff_can_see_the_schedule(client):
    staff = Account.objects.create_user(email="staff@example.com", is_staff=True)
    client.force_login(staff)

    resp = client.get("/admin/scheduled-tasks/")

    assert resp.status_code == 200
    assert b"compute-occurrences-nightly" in resp.content
    assert b"send-due-notifications-morning" in resp.content


def test_non_staff_cannot_see_the_schedule(client):
    account = Account.objects.create_user(email="not-staff@example.com")
    client.force_login(account)

    resp = client.get("/admin/scheduled-tasks/")

    assert resp.status_code == 302


def test_anonymous_cannot_see_the_schedule(client):
    resp = client.get("/admin/scheduled-tasks/")

    assert resp.status_code == 302


def test_toggle_mute_rejects_a_person_outside_your_family(client, two_families):
    family_a, family_b, account_a, _ = two_families
    person_b = Person.objects.create(family=family_b, first_name_en="Other", last_name_en="Family")
    event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BIRTHDAY)
    _login_as(client, account_a, family_a)

    resp = client.post(
        "/notifications/toggle/",
        {"person_id": person_b.uuid, "event_type_id": event_type.uuid, "channel": "email"},
    )

    assert resp.status_code == 404


def test_toggle_mute_rejects_the_broadcast_event_type(client, family):
    # Broadcast has no per-person override - see NotificationPreference.
    # clean() and AGENTS.md. No UI ever posts this, but the endpoint
    # itself should still refuse it.
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.BROADCAST)
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/toggle/",
        {"person_id": person.uuid, "event_type_id": event_type.uuid, "channel": "email"},
    )

    assert resp.status_code == 404


def test_toggle_mute_rejects_both_a_person_and_a_union(client, family, birthday_event_type):
    # NotificationPreference's own preference_not_both_person_and_union
    # constraint would otherwise turn this into an unhandled 500 instead of
    # a clean 404 - no legitimate form ever sends both.
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    person_b = Person.objects.create(family=family, first_name_en="Other", last_name_en="Person")
    union = Union.objects.create(person_a=person, person_b=person_b, status=Union.Status.MARRIED)
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/toggle/",
        {
            "person_id": person.uuid,
            "union_id": union.uuid,
            "event_type_id": birthday_event_type.uuid,
            "channel": "email",
        },
    )

    assert resp.status_code == 404


def test_toggle_mute_creates_a_person_override_when_none_exists(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/toggle/",
        {"person_id": person.uuid, "event_type_id": birthday_event_type.uuid, "channel": "email"},
    )

    assert resp.status_code == 302
    preference = NotificationPreference.objects.get(
        account=owner, person=person, event_type=birthday_event_type, channel="email"
    )
    # Subscribed by default (EventType.default_opt_in), so toggling flips
    # it to an explicit mute for just this person.
    assert preference.state == NotificationPreference.State.MUTED


def test_toggle_mute_removes_an_existing_override(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    NotificationPreference.objects.create(
        account=owner,
        person=person,
        event_type=birthday_event_type,
        channel="email",
        state=NotificationPreference.State.MUTED,
    )
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/toggle/",
        {"person_id": person.uuid, "event_type_id": birthday_event_type.uuid, "channel": "email"},
    )

    assert resp.status_code == 302
    assert not NotificationPreference.objects.filter(account=owner, person=person).exists()


def test_toggle_mute_creates_a_union_override(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    union = Union.objects.create(person_a=person_a, person_b=person_b, status=Union.Status.MARRIED)
    anniversary = EventType.objects.get(family=None, code=EventType.BuiltinCode.ANNIVERSARY)
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/toggle/",
        {"union_id": union.uuid, "event_type_id": anniversary.uuid, "channel": "email"},
    )

    assert resp.status_code == 302
    assert NotificationPreference.objects.filter(
        account=owner, union=union, event_type=anniversary, channel="email"
    ).exists()


def test_toggle_mute_redirects_to_dashboard_by_default(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/toggle/",
        {"person_id": person.uuid, "event_type_id": birthday_event_type.uuid, "channel": "email"},
    )

    assert resp.url == "/"


def test_owner_can_create_a_broadcast(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post("/broadcasts/new/", {"text": "Hello family", "send_at": "2030-01-01T10:00"})

    assert resp.status_code == 302
    broadcast = Broadcast.objects.get(family=family)
    assert broadcast.text == "Hello family"
    assert broadcast.created_by == owner
    assert list(broadcast.people.all()) == []


def test_broadcast_can_be_tied_to_multiple_people(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person")

    resp = client.post(
        "/broadcasts/new/",
        {"text": "Mazel tov", "send_at": "2030-01-01T10:00", "people": [person_a.pk, person_b.pk]},
    )

    assert resp.status_code == 302
    broadcast = Broadcast.objects.get(family=family)
    assert set(broadcast.people.all()) == {person_a, person_b}


def test_plain_member_cannot_create_a_broadcast(client, family):
    member_account = _member(family, FamilyMembership.Role.MEMBER)
    _login_as(client, member_account, family)

    resp = client.post("/broadcasts/new/", {"text": "Hello family", "send_at": "2030-01-01T10:00"})

    assert resp.status_code == 403
    assert not Broadcast.objects.exists()


def test_broadcast_people_are_scoped_to_the_current_family(client, two_families):
    family_a, family_b, account_a, _ = two_families
    Person.objects.create(family=family_b, first_name_en="Other", last_name_en="Family")
    _login_as(client, account_a, family_a)

    resp = client.get("/broadcasts/")

    assert resp.status_code == 200
    form = resp.context["form"]
    assert list(form.fields["people"].queryset) == []


def test_broadcast_people_picker_excludes_untracked_people(client, family):
    # An untracked person (notifications_enabled=False - a lineage-only
    # stub, see AGENTS.md) never gets notified of anything, so there's
    # nothing to pick them for here.
    owner = _member(family, FamilyMembership.Role.OWNER)
    tracked = Person.objects.create(family=family, first_name_en="Tracked", last_name_en="Person")
    untracked = Person.objects.create(
        family=family, first_name_en="Untracked", last_name_en="Person", notifications_enabled=False
    )
    _login_as(client, owner, family)

    resp = client.get("/broadcasts/")

    people = list(resp.context["form"].fields["people"].queryset)
    assert tracked in people
    assert untracked not in people


def test_broadcast_edit_keeps_an_already_tied_person_who_became_untracked(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person = Person.objects.create(family=family, first_name_en="Was", last_name_en="Tracked")
    broadcast = Broadcast.objects.create(family=family, text="Draft", created_by=owner)
    broadcast.people.add(person)
    person.notifications_enabled = False
    person.save(update_fields=["notifications_enabled"])
    _login_as(client, owner, family)

    resp = client.get(f"/broadcasts/{broadcast.uuid}/edit/")

    assert person in resp.context["form"].fields["people"].queryset


def test_broadcast_edit_page_is_reachable_before_it_sends(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    broadcast = Broadcast.objects.create(family=family, text="Draft", created_by=owner)

    resp = client.post(
        f"/broadcasts/{broadcast.uuid}/edit/", {"text": "Edited", "send_at": "2030-01-01T10:00"}
    )

    assert resp.status_code == 302
    broadcast.refresh_from_db()
    assert broadcast.text == "Edited"


def test_broadcast_cannot_be_edited_once_sent(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    broadcast = Broadcast.objects.create(
        family=family, text="Already sent", created_by=owner, is_sent=True, sent_at=timezone.now()
    )

    resp = client.post(
        f"/broadcasts/{broadcast.uuid}/edit/", {"text": "Edited", "send_at": "2030-01-01T10:00"}
    )

    assert resp.status_code == 404
    broadcast.refresh_from_db()
    assert broadcast.text == "Already sent"


def test_broadcast_can_be_cancelled_before_it_sends(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    broadcast = Broadcast.objects.create(family=family, text="Draft", created_by=owner)

    resp = client.post(f"/broadcasts/{broadcast.uuid}/delete/")

    assert resp.status_code == 302
    assert not Broadcast.objects.filter(pk=broadcast.pk).exists()


def test_broadcast_cannot_be_cancelled_once_sent(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    broadcast = Broadcast.objects.create(
        family=family, text="Already sent", created_by=owner, is_sent=True, sent_at=timezone.now()
    )

    resp = client.post(f"/broadcasts/{broadcast.uuid}/delete/")

    assert resp.status_code == 404
    assert Broadcast.objects.filter(pk=broadcast.pk).exists()


def test_broadcasts_page_only_shows_own_pending_broadcasts_to_an_editor(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    editor = _member(family, FamilyMembership.Role.EDITOR)
    _login_as(client, editor, family)
    Broadcast.objects.create(family=family, text="Owner draft", created_by=owner)
    Broadcast.objects.create(family=family, text="Editor draft", created_by=editor)

    resp = client.get("/broadcasts/")

    assert b"Owner draft" not in resp.content
    assert b"Editor draft" in resp.content


def test_broadcasts_page_shows_sent_broadcasts_to_everyone(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    editor = _member(family, FamilyMembership.Role.EDITOR)
    _login_as(client, editor, family)
    Broadcast.objects.create(
        family=family, text="Owner sent update", created_by=owner, is_sent=True, sent_at=timezone.now()
    )

    resp = client.get("/broadcasts/")

    assert b"Owner sent update" in resp.content


def test_broadcasts_page_shows_everyones_pending_broadcasts_to_a_superuser(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    superuser = Account.objects.create_superuser(email="root@example.com")
    FamilyMembership.objects.create(account=superuser, family=family, role=FamilyMembership.Role.EDITOR)
    _login_as(client, superuser, family)
    Broadcast.objects.create(family=family, text="Owner draft", created_by=owner)

    resp = client.get("/broadcasts/")

    assert b"Owner draft" in resp.content


def test_editor_cannot_edit_or_cancel_another_editors_pending_broadcast(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    editor = _member(family, FamilyMembership.Role.EDITOR)
    _login_as(client, editor, family)
    broadcast = Broadcast.objects.create(family=family, text="Owner draft", created_by=owner)

    edit_resp = client.post(
        f"/broadcasts/{broadcast.uuid}/edit/", {"text": "Hijacked", "send_at": "2030-01-01T10:00"}
    )
    delete_resp = client.post(f"/broadcasts/{broadcast.uuid}/delete/")

    assert edit_resp.status_code == 404
    assert delete_resp.status_code == 404
    broadcast.refresh_from_db()
    assert broadcast.text == "Owner draft"


def test_superuser_can_edit_another_editors_pending_broadcast(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    superuser = Account.objects.create_superuser(email="root@example.com")
    FamilyMembership.objects.create(account=superuser, family=family, role=FamilyMembership.Role.EDITOR)
    _login_as(client, superuser, family)
    broadcast = Broadcast.objects.create(family=family, text="Owner draft", created_by=owner)

    resp = client.post(
        f"/broadcasts/{broadcast.uuid}/edit/", {"text": "Fixed typo", "send_at": "2030-01-01T10:00"}
    )

    assert resp.status_code == 302
    broadcast.refresh_from_db()
    assert broadcast.text == "Fixed typo"


# --- Cross-tenant access: BroadcastUpdateView/BroadcastDeleteView must
# 404 for a broadcast outside the current workspace, even for an editor
# who could edit/cancel a broadcast in their own family - see
# tenants.mixins.FamilyScopedMixin and AGENTS.md. Person/Union's own
# versions of this live in family/tests/test_views.py.


@pytest.mark.parametrize(
    ["url_suffix", "method"],
    [
        ["edit/", "post"],
        ["delete/", "post"],
    ],
    ids=["broadcast update", "broadcast delete"],
)
def test_broadcast_endpoints_404_for_a_broadcast_outside_your_family(
    client, two_families, url_suffix, method
):
    family_a, family_b, account_a, account_b = two_families
    broadcast = Broadcast.objects.create(family=family_b, text="Not yours", created_by=account_b)
    _login_as(client, account_a, family_a)

    resp = getattr(client, method)(f"/broadcasts/{broadcast.uuid}/{url_suffix}")

    assert resp.status_code == 404
    broadcast.refresh_from_db()
    assert broadcast.text == "Not yours"


def test_broadcast_list_only_shows_your_own_familys_broadcasts(client, two_families):
    family_a, family_b, account_a, account_b = two_families
    Broadcast.objects.create(family=family_b, text="Family B's news", created_by=account_b)
    _login_as(client, account_a, family_a)

    resp = client.get("/broadcasts/")

    assert b"Family B" not in resp.content


def test_broadcast_list_splits_scheduled_and_sent(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    scheduled = Broadcast.objects.create(
        family=family,
        text="Not yet",
        created_by=owner,
        send_at=timezone.now() + dt.timedelta(days=1),
    )
    sent = Broadcast.objects.create(family=family, text="Already went out", created_by=owner, is_sent=True)

    resp = client.get("/broadcasts/")

    assert list(resp.context["scheduled_broadcasts"]) == [scheduled]
    assert list(resp.context["sent_broadcasts"]) == [sent]


def test_broadcast_list_orders_scheduled_soonest_first(client, family):
    # The opposite of Broadcast.Meta's own "-send_at" ordering - a
    # scheduled list reads naturally as "what's coming up next", not
    # newest-created-first.
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    later = Broadcast.objects.create(
        family=family, text="Later", created_by=owner, send_at=timezone.now() + dt.timedelta(days=5)
    )
    sooner = Broadcast.objects.create(
        family=family, text="Sooner", created_by=owner, send_at=timezone.now() + dt.timedelta(days=1)
    )

    resp = client.get("/broadcasts/")

    assert list(resp.context["scheduled_broadcasts"]) == [sooner, later]


def test_broadcast_list_orders_sent_most_recent_first(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    older = Broadcast.objects.create(
        family=family,
        text="Older",
        created_by=owner,
        is_sent=True,
        send_at=timezone.now() - dt.timedelta(days=5),
    )
    newer = Broadcast.objects.create(
        family=family,
        text="Newer",
        created_by=owner,
        is_sent=True,
        send_at=timezone.now() - dt.timedelta(days=1),
    )

    resp = client.get("/broadcasts/")

    assert list(resp.context["sent_broadcasts"]) == [newer, older]


def test_subscriptions_page_lists_every_family_event_type(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get("/notifications/")

    assert resp.status_code == 200
    event_types_shown = {row["event_type"] for row in resp.context["event_type_rows"]}
    assert birthday_event_type in event_types_shown


def test_subscriptions_page_reflects_a_whole_type_override(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    NotificationPreference.objects.create(
        account=owner, event_type=birthday_event_type, channel="email", state="muted"
    )

    resp = client.get("/notifications/")

    row = next(r for r in resp.context["event_type_rows"] if r["event_type"] == birthday_event_type)
    email_channel = next(c for c in row["channels"] if c["code"] == "email")
    assert email_channel["state"] == "muted"
    assert email_channel["is_override"] is True


def test_subscriptions_page_lists_a_person_specific_override(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    NotificationPreference.objects.create(
        account=owner, event_type=birthday_event_type, person=person, channel="email", state="muted"
    )

    resp = client.get("/notifications/")

    assert list(resp.context["overrides"]) == [
        NotificationPreference.objects.get(account=owner, person=person)
    ]


def test_update_event_type_preference_sets_a_whole_type_override(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/event-type/",
        {"event_type_id": birthday_event_type.uuid, "channel": "email", "state": "immediate_family_only"},
    )

    assert resp.status_code == 302
    preference = NotificationPreference.objects.get(
        account=owner,
        event_type=birthday_event_type,
        channel="email",
        person__isnull=True,
        union__isnull=True,
    )
    assert preference.state == "immediate_family_only"


def test_update_event_type_preference_reset_removes_the_override(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    NotificationPreference.objects.create(
        account=owner, event_type=birthday_event_type, channel="email", state="muted"
    )

    resp = client.post(
        "/notifications/event-type/",
        {"event_type_id": birthday_event_type.uuid, "channel": "email", "action": "reset"},
    )

    assert resp.status_code == 302
    assert not NotificationPreference.objects.filter(
        account=owner, event_type=birthday_event_type, channel="email"
    ).exists()


def test_update_event_type_preference_rejects_an_invalid_state(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/event-type/",
        {"event_type_id": birthday_event_type.uuid, "channel": "email", "state": "not-a-real-state"},
    )

    assert resp.status_code == 404


def test_update_event_type_preference_redirects_to_next_when_given(client, family, birthday_event_type):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/event-type/",
        {
            "event_type_id": birthday_event_type.uuid,
            "channel": "email",
            "state": "muted",
            "next": "/people/",
        },
    )

    assert resp.status_code == 302
    assert resp.url == "/people/"


def test_broadcast_create_re_renders_the_list_page_with_errors_when_invalid(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    existing = Broadcast.objects.create(family=family, text="Already scheduled", created_by=owner)
    _login_as(client, owner, family)

    resp = client.post("/broadcasts/new/", {"text": "", "send_at": "2030-01-01T10:00"})

    assert resp.status_code == 200
    assert resp.context["form"].errors
    assert list(resp.context["broadcasts"]) == [existing]
    assert not Broadcast.objects.filter(text="").exists()
