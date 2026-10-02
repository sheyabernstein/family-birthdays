import pytest
from django.core import mail

from accounts.models import Account
from family.models import Person, Suggestion
from family.tasks import send_suggestion_digests
from notifications.enums import ChannelEnum
from tenants.models import Family, FamilyMembership

pytestmark = pytest.mark.django_db

_counter = [0]


def _account(*, email: str | None = "", phone: str = "") -> Account:
    _counter[0] += 1
    if email == "" and not phone:
        return Account.objects.create_user(email=f"account-{_counter[0]}@example.com")
    return Account.objects.create_user(email=email or None, phone=phone or None)


def _member(family: Family, role: str, *, email: str | None = "", phone: str = "") -> Account:
    account = _account(email=email, phone=phone)
    FamilyMembership.objects.create(account=account, family=family, role=role)
    return account


def _pending_suggestion(family: Family, submitted_by: Account) -> Suggestion:
    target = Person.objects.create(family=family, first_name_he="Original")
    return Suggestion.objects.create(
        family=family,
        submitted_by=submitted_by,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=target,
        proposed_changes={"nickname": "New"},
    )


def test_owner_gets_a_digest_of_newly_pending_suggestions(family):
    _member(family, FamilyMembership.Role.OWNER, email="owner@example.com")
    submitter = _member(family, FamilyMembership.Role.MEMBER)
    suggestion = _pending_suggestion(family, submitter)

    send_suggestion_digests()

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["owner@example.com"]
    assert "1 suggestion to review" in mail.outbox[0].subject
    suggestion.refresh_from_db()
    assert suggestion.pending_message is not None
    assert suggestion.pending_message.channel == ChannelEnum.EMAIL
    assert suggestion.pending_message.destination == "owner@example.com"


def test_pending_digest_batches_several_suggestions_into_one_email_per_owner(family):
    _member(family, FamilyMembership.Role.EDITOR, email="editor@example.com")
    submitter = _member(family, FamilyMembership.Role.MEMBER)
    _pending_suggestion(family, submitter)
    _pending_suggestion(family, submitter)

    send_suggestion_digests()

    assert len(mail.outbox) == 1
    assert "2 suggestions to review" in mail.outbox[0].subject


def test_pending_digest_is_not_resent_for_an_already_notified_suggestion(family):
    _member(family, FamilyMembership.Role.OWNER, email="owner@example.com")
    submitter = _member(family, FamilyMembership.Role.MEMBER)
    _pending_suggestion(family, submitter)

    send_suggestion_digests()
    mail.outbox.clear()
    send_suggestion_digests()

    assert len(mail.outbox) == 0


def test_pending_digest_is_not_sent_to_a_plain_member(family):
    _member(family, FamilyMembership.Role.MEMBER, email="member@example.com")
    submitter = _member(family, FamilyMembership.Role.MEMBER)
    _pending_suggestion(family, submitter)

    send_suggestion_digests()

    assert len(mail.outbox) == 0


def test_pending_digest_falls_back_to_sms_for_an_email_less_owner(family):
    _member(family, FamilyMembership.Role.OWNER, email=None, phone="+15550001111")
    submitter = _member(family, FamilyMembership.Role.MEMBER)
    suggestion = _pending_suggestion(family, submitter)

    send_suggestion_digests()

    assert len(mail.outbox) == 0  # no email sent
    suggestion.refresh_from_db()
    assert suggestion.pending_message.channel == ChannelEnum.SMS
    assert suggestion.pending_message.destination == "+15550001111"
    assert "to review" in suggestion.pending_message.body
    # SMS is a short count+link, never the full per-suggestion breakdown.
    assert suggestion.subject_label not in suggestion.pending_message.body


def test_pending_digest_skips_an_owner_with_no_viable_channel(family):
    owner = _member(family, FamilyMembership.Role.OWNER, email=None, phone="+15550001111")
    owner.sms_notifications_enabled = False
    owner.save()
    submitter = _member(family, FamilyMembership.Role.MEMBER)
    _pending_suggestion(family, submitter)

    send_suggestion_digests()

    assert len(mail.outbox) == 0


def test_submitter_gets_a_digest_once_their_suggestion_is_reviewed(family):
    _member(family, FamilyMembership.Role.OWNER)
    submitter = _member(family, FamilyMembership.Role.MEMBER, email="submitter@example.com")
    suggestion = _pending_suggestion(family, submitter)
    suggestion.status = Suggestion.Status.APPROVED
    suggestion.save()

    send_suggestion_digests()

    outgoing = [msg for msg in mail.outbox if msg.to == ["submitter@example.com"]]
    assert len(outgoing) == 1
    assert "reviewed" in outgoing[0].subject.lower()
    suggestion.refresh_from_db()
    assert suggestion.resolved_message is not None
    assert suggestion.resolved_message.channel == ChannelEnum.EMAIL


def test_resolved_digest_groups_separately_per_submitter(family):
    _member(family, FamilyMembership.Role.OWNER)
    submitter_a = _member(family, FamilyMembership.Role.MEMBER, email="a@example.com")
    submitter_b = _member(family, FamilyMembership.Role.MEMBER, email="b@example.com")
    for suggestion in [_pending_suggestion(family, submitter_a), _pending_suggestion(family, submitter_b)]:
        suggestion.status = Suggestion.Status.REJECTED
        suggestion.save()

    send_suggestion_digests()

    resolved_recipients = {msg.to[0] for msg in mail.outbox if "reviewed" in msg.subject.lower()}
    assert resolved_recipients == {"a@example.com", "b@example.com"}


def test_resolved_digest_is_not_resent_for_an_already_notified_suggestion(family):
    _member(family, FamilyMembership.Role.OWNER)
    submitter = _member(family, FamilyMembership.Role.MEMBER, email="submitter@example.com")
    suggestion = _pending_suggestion(family, submitter)
    suggestion.status = Suggestion.Status.APPROVED
    suggestion.save()

    send_suggestion_digests()
    mail.outbox.clear()
    send_suggestion_digests()

    assert len(mail.outbox) == 0


def test_resolved_digest_falls_back_to_sms_for_an_email_less_submitter(family):
    _member(family, FamilyMembership.Role.OWNER)
    submitter = _member(family, FamilyMembership.Role.MEMBER, email=None, phone="+15550002222")
    suggestion = _pending_suggestion(family, submitter)
    suggestion.status = Suggestion.Status.APPROVED
    suggestion.save()

    send_suggestion_digests()

    assert len(mail.outbox) == 0
    suggestion.refresh_from_db()
    assert suggestion.resolved_message.channel == ChannelEnum.SMS
    assert suggestion.resolved_message.destination == "+15550002222"


def test_resolved_digest_skips_a_submitter_with_no_viable_channel(family):
    _member(family, FamilyMembership.Role.OWNER)
    submitter = _member(family, FamilyMembership.Role.MEMBER, email=None, phone="+15550002222")
    submitter.sms_notifications_enabled = False
    submitter.save()
    suggestion = _pending_suggestion(family, submitter)
    suggestion.status = Suggestion.Status.APPROVED
    suggestion.save()

    send_suggestion_digests()

    assert len(mail.outbox) == 0


def test_digests_are_scoped_to_their_own_family(two_families):
    family_a, family_b, owner_a, owner_b = two_families
    submitter_a = _member(family_a, FamilyMembership.Role.MEMBER)
    submitter_b = _member(family_b, FamilyMembership.Role.MEMBER)
    _pending_suggestion(family_a, submitter_a)
    _pending_suggestion(family_b, submitter_b)

    send_suggestion_digests()

    recipients = {msg.to[0] for msg in mail.outbox}
    assert recipients == {owner_a.email, owner_b.email}


def test_pending_digest_rolls_back_and_skips_dispatch_on_overlapping_claim(monkeypatch, family):
    _member(family, FamilyMembership.Role.OWNER, email="owner@example.com")
    submitter = _member(family, FamilyMembership.Role.MEMBER)
    suggestion = _pending_suggestion(family, submitter)

    import family.tasks as family_tasks
    from notifications.models import Message as MessageModel

    decoy = MessageModel.objects.create(
        direct_family=family, channel=ChannelEnum.EMAIL, destination="decoy@example.com", body="decoy"
    )
    real_render_digest = family_tasks._render_digest

    def racy_render_digest(*args, **kwargs):
        # Simulate an overlapping sweep claiming this exact suggestion
        # between our own read (already done by the time this runs) and
        # our own claiming update() below - has to land *before*
        # _send_pending_review_digests' own transaction.atomic() opens,
        # or this update would just be undone by the same rollback it's
        # supposed to be racing against.
        Suggestion.objects.filter(pk=suggestion.pk).update(pending_message=decoy)
        return real_render_digest(*args, **kwargs)

    monkeypatch.setattr(family_tasks, "_render_digest", racy_render_digest)

    send_suggestion_digests()

    assert len(mail.outbox) == 0
    suggestion.refresh_from_db()
    assert suggestion.pending_message_id == decoy.id
    # The Message rows this losing attempt built must have been rolled
    # back, not left behind as orphaned, already-sent-looking rows.
    assert MessageModel.objects.filter(direct_family=family).exclude(pk=decoy.pk).count() == 0


def test_resolved_digest_rolls_back_and_skips_dispatch_on_overlapping_claim(monkeypatch, family):
    _member(family, FamilyMembership.Role.OWNER)
    submitter = _member(family, FamilyMembership.Role.MEMBER, email="submitter@example.com")
    suggestion = _pending_suggestion(family, submitter)
    suggestion.status = Suggestion.Status.APPROVED
    suggestion.save()

    import family.tasks as family_tasks
    from notifications.models import Message as MessageModel

    decoy = MessageModel.objects.create(
        direct_family=family, channel=ChannelEnum.EMAIL, destination="decoy@example.com", body="decoy"
    )
    real_render_digest = family_tasks._render_digest

    def racy_render_digest(*args, **kwargs):
        Suggestion.objects.filter(pk=suggestion.pk).update(resolved_message=decoy)
        return real_render_digest(*args, **kwargs)

    monkeypatch.setattr(family_tasks, "_render_digest", racy_render_digest)

    send_suggestion_digests()

    assert len(mail.outbox) == 0
    suggestion.refresh_from_db()
    assert suggestion.resolved_message_id == decoy.id
    assert MessageModel.objects.filter(direct_family=family).exclude(pk=decoy.pk).count() == 0


def test_pending_digest_rolls_back_on_a_partial_overlap_not_just_a_full_one(monkeypatch, family):
    """A batch where only SOME suggestions got claimed by a concurrent sweep is just as unsafe as all of them -

    the already-rendered digest content still mentions the ones the
    other sweep claimed (and already sent its own digest about), so this
    run must still discard its own Message rows rather than send a
    partial-but-still-stale copy.
    """
    _member(family, FamilyMembership.Role.OWNER, email="owner@example.com")
    submitter = _member(family, FamilyMembership.Role.MEMBER)
    claimed_elsewhere = _pending_suggestion(family, submitter)
    still_unclaimed = _pending_suggestion(family, submitter)

    import family.tasks as family_tasks
    from notifications.models import Message as MessageModel

    decoy = MessageModel.objects.create(
        direct_family=family, channel=ChannelEnum.EMAIL, destination="decoy@example.com", body="decoy"
    )
    real_render_digest = family_tasks._render_digest

    def racy_render_digest(*args, **kwargs):
        # Only one of the two suggestions this run already read gets
        # claimed by the "other" sweep - a partial, not full, overlap.
        Suggestion.objects.filter(pk=claimed_elsewhere.pk).update(pending_message=decoy)
        return real_render_digest(*args, **kwargs)

    monkeypatch.setattr(family_tasks, "_render_digest", racy_render_digest)

    send_suggestion_digests()

    assert len(mail.outbox) == 0
    claimed_elsewhere.refresh_from_db()
    still_unclaimed.refresh_from_db()
    assert claimed_elsewhere.pending_message_id == decoy.id
    # Must NOT have been claimed by our own losing attempt either - that
    # attempt's whole transaction, including this row's own update(),
    # was rolled back.
    assert still_unclaimed.pending_message_id is None
    assert MessageModel.objects.filter(direct_family=family).exclude(pk=decoy.pk).count() == 0


def test_pending_digest_does_not_notify_a_deactivated_owner(family):
    owner = _member(family, FamilyMembership.Role.OWNER, email="owner@example.com")
    owner.is_active = False
    owner.save()
    submitter = _member(family, FamilyMembership.Role.MEMBER)
    _pending_suggestion(family, submitter)

    send_suggestion_digests()

    assert len(mail.outbox) == 0


def test_resolved_digest_does_not_notify_a_deactivated_submitter(family):
    _member(family, FamilyMembership.Role.OWNER)
    submitter = _member(family, FamilyMembership.Role.MEMBER, email="submitter@example.com")
    suggestion = _pending_suggestion(family, submitter)
    suggestion.status = Suggestion.Status.APPROVED
    suggestion.save()
    submitter.is_active = False
    submitter.save()

    send_suggestion_digests()

    assert len(mail.outbox) == 0
