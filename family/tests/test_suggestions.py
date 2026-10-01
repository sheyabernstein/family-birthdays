import pytest
from reversion.models import Version

from accounts.models import Account
from family.history import person_history
from family.models import Person, Suggestion, Union
from tenants.models import FamilyMembership

pytestmark = pytest.mark.django_db

_counter = [0]


def _account_counter():
    _counter[0] += 1
    return _counter[0]


def _member(family, role):
    account = Account.objects.create_user(email=f"{role}-{_account_counter()}@example.com")
    FamilyMembership.objects.create(account=account, family=family, role=role)
    return account


def _login_as(client, account, family):
    client.force_login(account)
    session = client.session
    session["family_id"] = family.id
    session.save()


def test_member_can_suggest_an_edit_for_a_visible_person(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    _login_as(client, member, family)
    target = Person.objects.create(
        family=family,
        first_name_he="Original",
        gender=Person.Gender.FEMALE,
        visibility=Person.Visibility.EVERYONE,
    )

    resp = client.post(
        "/suggestions/person/new/",
        {"person": str(target.uuid), "first_name_he": "Original", "gender": "F", "nickname": "Suggested"},
    )

    assert resp.status_code == 302
    suggestion = Suggestion.objects.get()
    assert suggestion.submitted_by_id == member.id
    assert suggestion.target_person_id == target.id
    assert suggestion.status == Suggestion.Status.PENDING
    assert suggestion.proposed_changes["nickname"] == "Suggested"
    # Nothing applied yet - submission never saves the target directly.
    target.refresh_from_db()
    assert target.nickname == ""


def test_member_cannot_suggest_an_edit_for_an_invisible_person(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    _login_as(client, member, family)
    target = Person.objects.create(family=family, first_name_he="Hidden", visibility=Person.Visibility.NOBODY)

    resp = client.get(f"/suggestions/person/new/?person={target.uuid}")
    assert resp.status_code == 404

    resp = client.post("/suggestions/person/new/", {"person": str(target.uuid), "first_name_he": "Hidden"})
    assert resp.status_code == 404
    assert not Suggestion.objects.exists()


def test_review_queue_edit_link_prefills_the_real_edit_form_and_applying_it_closes_out_the_suggestion(
    client, family
):
    member = _member(family, FamilyMembership.Role.MEMBER)
    owner = _member(family, FamilyMembership.Role.OWNER)
    target = Person.objects.create(
        family=family, first_name_he="Target", visibility=Person.Visibility.EVERYONE
    )
    suggestion = Suggestion.objects.create(
        family=family,
        submitted_by=member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=target,
        proposed_changes={"first_name_he": "Target", "nickname": "Approved Nickname"},
    )

    _login_as(client, owner, family)

    # The reviewer opens the real edit page, prefilled from the suggestion.
    resp = client.get(f"/people/{target.uuid}/edit/?suggestion={suggestion.uuid}")
    assert resp.status_code == 200
    assert resp.context["form"].initial.get("nickname") == "Approved Nickname"
    assert resp.context["applying_suggestion"] == suggestion

    # Saving it - a completely ordinary PersonUpdateView POST, plus the
    # hidden suggestion field the template adds.
    resp = client.post(
        f"/people/{target.uuid}/edit/",
        {
            "first_name_he": "Target",
            "nickname": "Approved Nickname",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
            "suggestion": str(suggestion.uuid),
        },
    )
    assert resp.status_code == 302

    target.refresh_from_db()
    assert target.nickname == "Approved Nickname"
    suggestion.refresh_from_db()
    assert suggestion.status == Suggestion.Status.APPROVED
    assert suggestion.reviewed_by_id == owner.id
    assert suggestion.applied_revision_id is not None

    # revision.user is the reviewer (ordinary reversion attribution) -
    # attribution to the original submitter lives on the Suggestion, and
    # the History card surfaces it from there.
    version = Version.objects.get_for_object(target).first()
    assert version.revision.user == owner

    events = person_history(target, unions=[])
    assert any(e.via_suggestion_from == member for e in events)


def test_applying_a_person_add_suggestion_creates_a_new_person(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    owner = _member(family, FamilyMembership.Role.OWNER)
    suggestion = Suggestion.objects.create(
        family=family,
        submitted_by=member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=None,
        proposed_changes={"first_name_he": "חדש", "first_name_en": "New", "gender": "M"},
    )

    _login_as(client, owner, family)
    resp = client.get(f"/people/new/?suggestion={suggestion.uuid}")
    assert resp.context["form"].initial.get("first_name_en") == "New"

    resp = client.post(
        f"/people/new/?suggestion={suggestion.uuid}",
        {
            "first_name_he": "חדש",
            "first_name_en": "New",
            "gender": "M",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
            "suggestion": str(suggestion.uuid),
        },
    )
    assert resp.status_code == 302

    new_person = Person.objects.get(first_name_en="New")
    assert new_person.family_id == family.id
    suggestion.refresh_from_db()
    assert suggestion.status == Suggestion.Status.APPROVED


def test_combined_person_and_union_suggestion_is_a_two_step_flow(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    owner = _member(family, FamilyMembership.Role.OWNER)
    spouse = Person.objects.create(
        family=family,
        first_name_he="Spouse",
        gender=Person.Gender.MALE,
        visibility=Person.Visibility.EVERYONE,
    )
    suggestion = Suggestion.objects.create(
        family=family,
        submitted_by=member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=None,
        proposed_changes={"first_name_he": "כלה", "first_name_en": "Bride", "gender": "F"},
        link_kind=Suggestion.LinkKind.SPOUSE,
        link_with=spouse,
        link_union_changes={"status": Union.Status.MARRIED},
    )

    _login_as(client, owner, family)

    # Step 1: create the person.
    resp = client.post(
        f"/people/new/?suggestion={suggestion.uuid}",
        {
            "first_name_he": "כלה",
            "first_name_en": "Bride",
            "gender": "F",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
            "suggestion": str(suggestion.uuid),
        },
    )
    assert resp.status_code == 302
    new_person = Person.objects.get(first_name_en="Bride")

    suggestion.refresh_from_db()
    assert suggestion.status == Suggestion.Status.PENDING  # not done yet
    assert suggestion.resulting_person_id == new_person.id
    assert suggestion.applied_revision_id is not None
    assert resp.url == f"/people/{spouse.uuid}/spouse/new/?suggestion={suggestion.uuid}"

    # Step 2: create the union, redirected to automatically.
    resp = client.get(resp.url)
    assert resp.status_code == 200
    assert resp.context["form"].initial.get("existing_spouse") == new_person.pk

    resp = client.post(
        f"/people/{spouse.uuid}/spouse/new/",
        {"existing_spouse": new_person.pk, "status": "married", "suggestion": str(suggestion.uuid)},
    )
    assert resp.status_code == 302

    union = Union.objects.get()
    assert {union.person_a_id, union.person_b_id} == {spouse.id, new_person.id}
    suggestion.refresh_from_db()
    assert suggestion.status == Suggestion.Status.APPROVED
    assert suggestion.link_applied_revision_id is not None


def test_combined_person_and_father_link_suggestion_is_a_two_step_flow(client, family):
    """Same two-step shape as the spouse case, but the second leg updates an existing person's own field."""
    member = _member(family, FamilyMembership.Role.MEMBER)
    owner = _member(family, FamilyMembership.Role.OWNER)
    child = Person.objects.create(family=family, first_name_he="Child", visibility=Person.Visibility.EVERYONE)
    suggestion = Suggestion.objects.create(
        family=family,
        submitted_by=member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=None,
        proposed_changes={"first_name_he": "אבא", "first_name_en": "Dad", "gender": "M"},
        link_kind=Suggestion.LinkKind.FATHER,
        link_with=child,
    )

    _login_as(client, owner, family)

    resp = client.post(
        f"/people/new/?suggestion={suggestion.uuid}",
        {
            "first_name_he": "אבא",
            "first_name_en": "Dad",
            "gender": "M",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
            "suggestion": str(suggestion.uuid),
        },
    )
    assert resp.status_code == 302
    new_person = Person.objects.get(first_name_en="Dad")

    suggestion.refresh_from_db()
    assert suggestion.status == Suggestion.Status.PENDING
    assert suggestion.resulting_person_id == new_person.id
    assert resp.url == f"/people/{child.uuid}/edit/?suggestion={suggestion.uuid}"

    resp = client.get(resp.url)
    assert resp.status_code == 200
    assert resp.context["form"].initial.get("father") == new_person.pk

    resp = client.post(
        f"/people/{child.uuid}/edit/",
        {
            "first_name_he": "Child",
            "father": new_person.pk,
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
            "suggestion": str(suggestion.uuid),
        },
    )
    assert resp.status_code == 302

    child.refresh_from_db()
    assert child.father_id == new_person.id
    suggestion.refresh_from_db()
    assert suggestion.status == Suggestion.Status.APPROVED
    assert suggestion.link_applied_revision_id is not None


def test_combined_person_and_father_link_suggestion_works_for_a_cross_family_link_with(
    client, family, two_families
):
    """link_with can be a cross-family in-law (visible via marriage) - step 2 must not 404.

    Regression test for a bug where PersonUpdateView's normal
    FamilyScopedMixin filter (same family only) made step 2 of a
    father/mother link suggestion permanently unreachable whenever
    link_with belonged to a different family than the reviewer's own -
    see PersonUpdateView.get_object's ?suggestion= relaxation.
    """
    other_family, _, _, _ = two_families
    member = _member(family, FamilyMembership.Role.MEMBER)
    owner = _member(family, FamilyMembership.Role.OWNER)
    in_law = Person.objects.create(family=other_family, first_name_he="InLaw", gender=Person.Gender.FEMALE)
    spouse_in_family = Person.objects.create(family=family, first_name_he="Spouse", gender=Person.Gender.MALE)
    Union.objects.create(person_a=spouse_in_family, person_b=in_law, status=Union.Status.MARRIED)

    suggestion = Suggestion.objects.create(
        family=family,
        submitted_by=member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=None,
        proposed_changes={"first_name_he": "אבא", "first_name_en": "Dad", "gender": "M"},
        link_kind=Suggestion.LinkKind.FATHER,
        link_with=in_law,
    )

    _login_as(client, owner, family)

    resp = client.post(
        f"/people/new/?suggestion={suggestion.uuid}",
        {
            "first_name_he": "אבא",
            "first_name_en": "Dad",
            "gender": "M",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
            "suggestion": str(suggestion.uuid),
        },
    )
    assert resp.status_code == 302
    new_person = Person.objects.get(first_name_en="Dad")
    assert resp.url == f"/people/{in_law.uuid}/edit/?suggestion={suggestion.uuid}"

    # Step 2 lands on PersonUpdateView for a person from a DIFFERENT
    # family - this must not 404.
    resp = client.get(resp.url)
    assert resp.status_code == 200
    assert resp.context["form"].initial.get("father") == new_person.pk

    resp = client.post(
        f"/people/{in_law.uuid}/edit/",
        {
            "first_name_he": "InLaw",
            "father": new_person.pk,
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
            "suggestion": str(suggestion.uuid),
        },
    )
    assert resp.status_code == 302

    in_law.refresh_from_db()
    assert in_law.father_id == new_person.id
    suggestion.refresh_from_db()
    assert suggestion.status == Suggestion.Status.APPROVED


def test_cannot_reject_or_withdraw_a_suggestion_once_its_person_has_already_been_created(client, family):
    """Once step 1 of a two-step suggestion creates a real Person, reject/withdraw must refuse.

    Regression test - the Suggestion row is the only record of where
    that Person came from; rejecting or withdrawing it (status change or
    outright delete) would silently orphan the Person with no trace.
    """
    member = _member(family, FamilyMembership.Role.MEMBER)
    owner = _member(family, FamilyMembership.Role.OWNER)
    spouse = Person.objects.create(family=family, first_name_he="Spouse", gender=Person.Gender.MALE)
    resulting_person = Person.objects.create(
        family=family, first_name_he="AlreadyCreated", gender=Person.Gender.FEMALE
    )
    suggestion = Suggestion.objects.create(
        family=family,
        submitted_by=member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=None,
        proposed_changes={"first_name_he": "כלה", "gender": "F"},
        link_kind=Suggestion.LinkKind.SPOUSE,
        link_with=spouse,
        resulting_person=resulting_person,
    )

    _login_as(client, owner, family)
    resp = client.post(f"/suggestions/{suggestion.uuid}/reject/", {"reviewer_note": "whoops"})
    assert resp.status_code == 302
    suggestion.refresh_from_db()
    assert suggestion.status == Suggestion.Status.PENDING

    _login_as(client, member, family)
    resp = client.post(f"/suggestions/{suggestion.uuid}/withdraw/")
    assert resp.status_code == 302
    assert Suggestion.objects.filter(pk=suggestion.pk).exists()


def test_a_suggestion_from_another_family_404s_when_applied(client, family, two_families):
    _, _, other_owner, _ = two_families
    other_member = _member(family, FamilyMembership.Role.MEMBER)
    target = Person.objects.create(family=family, first_name_he="Target")
    suggestion = Suggestion.objects.create(
        family=family,
        submitted_by=other_member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=target,
        proposed_changes={"first_name_he": "Target", "nickname": "Whatever"},
    )

    _login_as(client, other_owner, two_families[0])
    resp = client.get(f"/people/{target.uuid}/edit/?suggestion={suggestion.uuid}")
    assert resp.status_code == 404


def test_rejecting_a_suggestion_sets_status_and_reviewer_note(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    member = _member(family, FamilyMembership.Role.MEMBER)
    target = Person.objects.create(family=family, first_name_he="Target")
    suggestion = Suggestion.objects.create(
        family=family,
        submitted_by=member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=target,
        proposed_changes={"first_name_he": "Target", "nickname": "Whatever"},
    )

    _login_as(client, owner, family)
    resp = client.post(f"/suggestions/{suggestion.uuid}/reject/", {"reviewer_note": "Not accurate"})
    assert resp.status_code == 302

    suggestion.refresh_from_db()
    assert suggestion.status == Suggestion.Status.REJECTED
    assert suggestion.reviewer_note == "Not accurate"
    assert suggestion.reviewed_by_id == owner.id


def test_submitter_can_withdraw_their_own_pending_suggestion_but_not_someone_elses(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    other_member = _member(family, FamilyMembership.Role.MEMBER)
    target = Person.objects.create(family=family, first_name_he="Target")
    suggestion = Suggestion.objects.create(
        family=family,
        submitted_by=member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=target,
        proposed_changes={"first_name_he": "Target", "nickname": "Whatever"},
    )

    _login_as(client, other_member, family)
    resp = client.post(f"/suggestions/{suggestion.uuid}/withdraw/")
    assert resp.status_code == 302
    assert Suggestion.objects.filter(pk=suggestion.pk).exists()

    _login_as(client, member, family)
    resp = client.post(f"/suggestions/{suggestion.uuid}/withdraw/")
    assert resp.status_code == 302
    assert not Suggestion.objects.filter(pk=suggestion.pk).exists()


def test_suggestions_page_shows_the_review_queue_only_to_editors(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    owner = _member(family, FamilyMembership.Role.OWNER)
    target = Person.objects.create(family=family, first_name_he="Target")
    Suggestion.objects.create(
        family=family,
        submitted_by=member,
        target_model=Suggestion.TargetModel.PERSON,
        target_person=target,
        proposed_changes={"first_name_he": "Target", "nickname": "Whatever"},
    )

    _login_as(client, member, family)
    resp = client.get("/suggestions/")
    assert resp.status_code == 200
    assert "review_queue" not in resp.context
    assert len(resp.context["my_suggestions"]) == 1

    _login_as(client, owner, family)
    resp = client.get("/suggestions/")
    assert resp.status_code == 200
    assert len(resp.context["review_queue"]) == 1
