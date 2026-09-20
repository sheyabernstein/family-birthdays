import datetime as dt

import pytest
from django.db import connection
from django.template.defaultfilters import date as date_filter
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Account
from family.hebrew import gregorian_to_hebrew
from family.models import Person, Union
from notifications.models import Broadcast, EventType, Occurrence
from notifications.tasks import compute_occurrences_for_union
from tenants.models import Family, FamilyMembership

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


def _years_ago(years):
    return timezone.localdate() - dt.timedelta(days=int(years * 365.25))


def test_owner_can_create_a_person(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        "/people/new/",
        {
            "first_name_en": "New",
            "last_name_en": "Person",
            "is_living": "on",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
    )

    person = Person.objects.get(first_name_en="New", last_name_en="Person")
    assert person.family_id == family.id
    assert resp.status_code == 302
    assert resp.url == f"/people/{person.uuid}/"


def test_editor_can_create_a_person(client, family):
    editor = _member(family, FamilyMembership.Role.EDITOR)
    _login_as(client, editor, family)

    resp = client.post(
        "/people/new/",
        {
            "first_name_en": "New",
            "last_name_en": "Person",
            "is_living": "on",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
    )

    assert resp.status_code == 302
    assert Person.objects.filter(first_name_en="New", last_name_en="Person").exists()


def test_plain_member_cannot_create_a_person(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    _login_as(client, member, family)

    resp = client.get("/people/new/")

    assert resp.status_code == 403
    assert not Person.objects.filter(first_name_en="New").exists()


@pytest.mark.parametrize(
    ["role", "expected_status", "person_survives"],
    [
        [FamilyMembership.Role.OWNER, 302, False],
        [FamilyMembership.Role.EDITOR, 403, True],
    ],
    ids=[
        "owner can delete a person",
        "editor cannot delete a person",
    ],
)
def test_delete_person_permission_by_role(client, family, role, expected_status, person_survives):
    member = _member(family, role)
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _login_as(client, member, family)

    resp = client.post(f"/people/{person.uuid}/delete/")

    assert resp.status_code == expected_status
    assert Person.objects.filter(pk=person.pk).exists() == person_survives


@pytest.mark.parametrize(
    ["url_suffix"],
    [
        ["edit/"],
        ["spouse/new/"],
    ],
    ids=[
        "cannot edit a person from another family",
        "cannot add a spouse for a person outside your family",
    ],
)
def test_person_endpoints_404_for_a_person_outside_your_family(client, family, url_suffix):
    other_family = Family.objects.create(name="Other Family")
    other_person = Person.objects.create(family=other_family, first_name_en="Not", last_name_en="Yours")
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get(f"/people/{other_person.uuid}/{url_suffix}")

    assert resp.status_code == 404


def test_father_choices_are_scoped_to_the_current_family(client, family):
    other_family = Family.objects.create(name="Other Family")
    Person.objects.create(family=other_family, first_name_en="Outside", last_name_en="Family")
    own_person = Person.objects.create(
        family=family, first_name_en="Inside", last_name_en="Family", gender=Person.Gender.MALE
    )
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get("/people/new/")

    father_ids = {p.pk for p in resp.context["form"].fields["father"].queryset}
    assert father_ids == {own_person.pk}


def test_father_and_mother_choices_are_filtered_by_gender(client, family):
    man = Person.objects.create(
        family=family, first_name_en="Man", last_name_en="Person", gender=Person.Gender.MALE
    )
    woman = Person.objects.create(
        family=family, first_name_en="Woman", last_name_en="Person", gender=Person.Gender.FEMALE
    )
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get("/people/new/")

    form = resp.context["form"]
    assert {p.pk for p in form.fields["father"].queryset} == {man.pk}
    assert {p.pk for p in form.fields["mother"].queryset} == {woman.pk}


def test_father_choices_exclude_the_persons_own_descendants(client, family):
    grandfather = Person.objects.create(
        family=family, first_name_en="Grandfather", last_name_en="Rokach", gender=Person.Gender.MALE
    )
    father = Person.objects.create(
        family=family,
        first_name_en="Father",
        last_name_en="Rokach",
        gender=Person.Gender.MALE,
        father=grandfather,
    )
    unrelated_man = Person.objects.create(
        family=family, first_name_en="Unrelated", last_name_en="Man", gender=Person.Gender.MALE
    )
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get(f"/people/{grandfather.uuid}/edit/")

    father_ids = {p.pk for p in resp.context["form"].fields["father"].queryset}
    assert father.pk not in father_ids
    assert unrelated_man.pk in father_ids


def test_father_field_keeps_a_wrong_gender_value_already_on_file(client, family):
    # Pre-existing bad data (e.g. from an import) shouldn't become
    # invisible - and unsaveable-without-wiping - just because the
    # dropdown is now gender-filtered. See family.forms._parent_queryset.
    mother_stored_as_father = Person.objects.create(
        family=family, first_name_en="Wrongly", last_name_en="Filed", gender=Person.Gender.FEMALE
    )
    child = Person.objects.create(
        family=family, first_name_en="Child", last_name_en="Rokach", father=mother_stored_as_father
    )
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get(f"/people/{child.uuid}/edit/")

    father_ids = {p.pk for p in resp.context["form"].fields["father"].queryset}
    assert mother_stored_as_father.pk in father_ids
    label = resp.context["form"].fields["father"].label_from_instance(mother_stored_as_father)
    assert "wrong gender on file" in label


@pytest.mark.parametrize(
    ["role", "history_visible"],
    [
        [FamilyMembership.Role.EDITOR, True],
        [FamilyMembership.Role.MEMBER, False],
    ],
    ids=[
        "history shows for an editor",
        "history hides for a plain member",
    ],
)
def test_person_detail_history_visibility_by_role(client, family, role, history_visible):
    member = _member(family, role)
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    _login_as(client, member, family)

    resp = client.get(f"/people/{person.uuid}/")

    assert ("history_events" in resp.context) is history_visible


def test_person_detail_hides_birth_year_from_a_plain_member(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_gregorian=dt.date(1990, 3, 4),
        dob_hebrew_year=5750,
        dob_hebrew_month=6,
        dob_hebrew_day=7,
    )
    _login_as(client, member, family)

    resp = client.get(f"/people/{person.uuid}/")

    assert resp.context["can_see_birth_year"] is False
    assert b"1990" not in resp.content
    # Day-of-week is tied to a specific year (e.g. "Sunday, March 4" only
    # holds for one particular year) - showing it while hiding the year
    # would narrow the year right back down, since a weekday/month/day
    # combination only recurs every ~28 years.
    assert b"Sunday" not in resp.content
    assert b"March 4" in resp.content


def test_person_detail_shows_birth_year_to_an_editor(client, family):
    editor = _member(family, FamilyMembership.Role.EDITOR)
    person = Person.objects.create(
        family=family, first_name_en="Test", last_name_en="Person", dob_gregorian=dt.date(1990, 3, 4)
    )
    _login_as(client, editor, family)

    resp = client.get(f"/people/{person.uuid}/")

    assert resp.context["can_see_birth_year"] is True
    assert b"1990" in resp.content


def test_person_detail_shows_birth_year_to_a_member_viewing_their_own_record(client, family):
    account = Account.objects.create_user(email="me@example.com")
    FamilyMembership.objects.create(account=account, family=family, role=FamilyMembership.Role.MEMBER)
    person = Person.objects.create(
        family=family,
        first_name_en="My",
        last_name_en="Self",
        account=account,
        dob_gregorian=dt.date(1990, 3, 4),
    )
    _login_as(client, account, family)

    resp = client.get(f"/people/{person.uuid}/")

    assert resp.context["can_see_birth_year"] is True
    assert b"1990" in resp.content


@pytest.mark.parametrize(
    ["gender", "event_code", "dob_kind", "years_ago", "expected_present"],
    [
        [Person.Gender.MALE, EventType.BuiltinCode.BAR_MITZVAH, "gregorian", 40, False],
        [Person.Gender.MALE, EventType.BuiltinCode.BAR_MITZVAH, "gregorian", 10, True],
        [Person.Gender.FEMALE, EventType.BuiltinCode.BAT_MITZVAH, "gregorian", 40, False],
        [Person.Gender.MALE, EventType.BuiltinCode.BAR_MITZVAH, "none", None, True],
        [Person.Gender.MALE, EventType.BuiltinCode.BAR_MITZVAH, "hebrew", 40, False],
    ],
    ids=[
        "bar mitzvah hides once a boy has already passed the age",
        "bar mitzvah shows for a boy who has not reached the age yet",
        "bat mitzvah hides once a woman has already passed the age",
        "bar mitzvah still shows without a known date of birth - can't prove it's passed",
        "bar mitzvah hides for a hebrew-only dob past the age",
    ],
)
def test_bar_or_bat_mitzvah_toggle_visibility(
    client, family, gender, event_code, dob_kind, years_ago, expected_present
):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    dob_kwargs = {}
    if dob_kind == "gregorian":
        dob_kwargs["dob_gregorian"] = _years_ago(years_ago)
    elif dob_kind == "hebrew":
        # person.age is Gregorian-only and None here - the cutoff has to
        # fall back to Hebrew-year math (see person_has_passed_coming_of_age),
        # not silently never fire just because dob_gregorian isn't set.
        today_hebrew_year = gregorian_to_hebrew(timezone.localdate()).year
        dob_kwargs["dob_hebrew_year"] = today_hebrew_year - years_ago
    person = Person.objects.create(
        family=family, first_name_en="Test", last_name_en="Person", gender=gender, **dob_kwargs
    )

    resp = client.get(f"/people/{person.uuid}/")

    codes = {row["event_type"].code for row in resp.context["event_rows"]}
    assert (event_code in codes) == expected_present


def test_person_create_prefills_father_and_mother_from_query_params(client, family):
    # This is the "+ Add child" placeholder from the family tree - see
    # family_tree.html's refreshPlaceholders().
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    father = Person.objects.create(family=family, first_name_en="Dad", last_name_en="Test")
    mother = Person.objects.create(family=family, first_name_en="Mom", last_name_en="Test")

    resp = client.get(f"/people/new/?father={father.uuid}&mother={mother.uuid}")

    assert resp.context["form"].initial["father"] == father.pk
    assert resp.context["form"].initial["mother"] == mother.pk


def test_person_create_link_as_father_auto_links_the_new_person(client, family):
    # The "+ Add father" placeholder - the new person doesn't own the
    # relationship, so this has to happen as a side effect of the save.
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    child = Person.objects.create(family=family, first_name_en="Kid", last_name_en="Test")

    resp = client.post(
        f"/people/new/?link_as=father&link_of={child.uuid}",
        {
            "first_name_en": "New",
            "last_name_en": "Father",
            "gender": Person.Gender.MALE,
            "link_as": "father",
            "link_of": str(child.uuid),
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
    )

    new_father = Person.objects.get(first_name_en="New", last_name_en="Father")
    child.refresh_from_db()
    assert child.father_id == new_father.id
    assert resp.status_code == 302


def test_person_create_link_of_is_scoped_to_your_own_family(client, family):
    other_family = Family.objects.create(name="Other Family")
    other_person = Person.objects.create(family=other_family, first_name_en="Not", last_name_en="Yours")
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        f"/people/new/?link_as=father&link_of={other_person.uuid}",
        {
            "first_name_en": "New",
            "last_name_en": "Father",
            "link_as": "father",
            "link_of": str(other_person.uuid),
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
    )

    assert resp.status_code == 404


def test_person_create_redirects_to_next_when_given(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        "/people/new/",
        {
            "first_name_en": "New",
            "last_name_en": "Person",
            "next": "/some/place/",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
    )

    assert resp.status_code == 302
    assert resp.url == "/some/place/"


def test_person_detail_404s_for_a_person_outside_your_family_and_unrelated(client, two_families):
    family_a, family_b, account_a, _ = two_families
    person_b = Person.objects.create(family=family_b, first_name_en="Other", last_name_en="Family")
    _login_as(client, account_a, family_a)

    resp = client.get(f"/people/{person_b.uuid}/")

    assert resp.status_code == 404


def test_person_detail_200s_for_an_in_law_through_marriage(client, two_families):
    family_a, family_b, account_a, _ = two_families
    person_a = Person.objects.create(family=family_a, first_name_en="Mine", last_name_en="Family")
    person_b = Person.objects.create(family=family_b, first_name_en="Married", last_name_en="In")
    Union.objects.create(person_a=person_a, person_b=person_b)
    _login_as(client, account_a, family_a)

    resp = client.get(f"/people/{person_b.uuid}/")

    assert resp.status_code == 200


def test_birthday_and_bar_mitzvah_toggles_hide_and_yahrzeit_shows_for_a_deceased_person(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    deceased = Person.objects.create(
        family=family,
        first_name_en="Late",
        last_name_en="Person",
        gender=Person.Gender.MALE,
        dob_gregorian=_years_ago(10),
        dod_gregorian=timezone.localdate() - dt.timedelta(days=1),
    )

    resp = client.get(f"/people/{deceased.uuid}/")

    codes = {row["event_type"].code for row in resp.context["event_rows"]}
    assert EventType.BuiltinCode.BIRTHDAY not in codes
    assert EventType.BuiltinCode.BAR_MITZVAH not in codes
    assert EventType.BuiltinCode.YAHRZEIT in codes


def test_birthday_toggle_still_shows_for_a_deceased_person_with_no_dob_at_all(client, family):
    # Mirrors the real bug this was caught from - a lineage stub with no
    # birth data recorded at all should still lose Birthday once dead,
    # not just when a dob happens to be on file.
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    deceased = Person.objects.create(
        family=family,
        first_name_en="Late",
        last_name_en="NoDob",
        gender=Person.Gender.MALE,
        dod_gregorian=timezone.localdate() - dt.timedelta(days=1),
    )

    resp = client.get(f"/people/{deceased.uuid}/")

    codes = {row["event_type"].code for row in resp.context["event_rows"]}
    assert EventType.BuiltinCode.BIRTHDAY not in codes


def test_anniversary_toggle_hides_once_a_spouse_has_died(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(
        family=family,
        first_name_en="B",
        last_name_en="Test",
        dod_gregorian=timezone.localdate() - dt.timedelta(days=1),
    )
    Union.objects.create(person_a=person_a, person_b=person_b)
    _login_as(client, owner, family)

    resp = client.get(f"/people/{person_a.uuid}/")

    assert resp.context["union_rows"] == []


def test_recording_a_death_clears_the_persons_future_anniversary_occurrences(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
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
    _login_as(client, owner, family)

    resp = client.post(
        f"/people/{person_a.uuid}/edit/",
        {
            "first_name_en": "A",
            "last_name_en": "Test",
            "dod_gregorian": (timezone.localdate() - dt.timedelta(days=1)).isoformat(),
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
    )

    assert resp.status_code == 302
    assert not Occurrence.objects.filter(
        union=union, event_type__code=EventType.BuiltinCode.ANNIVERSARY, is_sent=False
    ).exists()


@pytest.mark.parametrize(
    ["marriage_days_offset", "expected_code", "unexpected_code"],
    [
        [30, EventType.BuiltinCode.WEDDING, EventType.BuiltinCode.ANNIVERSARY],
        [-30, EventType.BuiltinCode.ANNIVERSARY, EventType.BuiltinCode.WEDDING],
    ],
    ids=[
        "wedding shows and anniversary hides for an upcoming union",
        "wedding hides and anniversary shows once already married",
    ],
)
def test_wedding_vs_anniversary_toggle_visibility(
    client, family, marriage_days_offset, expected_code, unexpected_code
):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    Union.objects.create(
        person_a=person_a,
        person_b=person_b,
        marriage_date_gregorian=timezone.localdate() + dt.timedelta(days=marriage_days_offset),
    )
    _login_as(client, owner, family)

    resp = client.get(f"/people/{person_a.uuid}/")

    codes = {row["event_type"].code for row in resp.context["union_rows"]}
    assert expected_code in codes
    assert unexpected_code not in codes


def test_untracked_person_has_no_notify_me_toggles(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_gregorian=dt.date(1990, 1, 1),
        notifications_enabled=False,
    )

    resp = client.get(f"/people/{person.uuid}/")

    assert resp.context["event_rows"] == []


def test_untracked_persons_union_has_no_anniversary_toggles(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(
        family=family, first_name_en="B", last_name_en="Test", notifications_enabled=False
    )
    Union.objects.create(person_a=person_a, person_b=person_b)

    resp = client.get(f"/people/{person_a.uuid}/")

    assert resp.context["union_rows"] == []


@pytest.mark.parametrize(
    ["role", "message_shown"],
    [
        [FamilyMembership.Role.OWNER, True],
        [FamilyMembership.Role.EDITOR, True],
        [FamilyMembership.Role.MEMBER, False],
    ],
    ids=[
        "owner sees the untracked explanation",
        "editor sees the untracked explanation",
        "plain member sees no notify-me card at all",
    ],
)
def test_untracked_person_notify_card_visibility_by_role(client, family, role, message_shown):
    viewer = _member(family, role)
    _login_as(client, viewer, family)
    person = Person.objects.create(
        family=family, first_name_en="Test", last_name_en="Person", notifications_enabled=False
    )

    resp = client.get(f"/people/{person.uuid}/")

    assert (b"isn't tracked for notifications" in resp.content) == message_shown


def test_person_list_only_shows_your_own_family(client, two_families):
    family_a, family_b, account_a, _ = two_families
    Person.objects.create(family=family_a, first_name_en="Mine", last_name_en="Family")
    Person.objects.create(family=family_b, first_name_en="Other", last_name_en="Family")
    _login_as(client, account_a, family_a)

    resp = client.get("/people/")

    body = resp.content.decode()
    assert "Mine Family" in body
    assert "Other Family" not in body


def test_person_list_hides_untracked_people_by_default(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    Person.objects.create(family=family, first_name_en="Tracked", last_name_en="Person")
    Person.objects.create(
        family=family, first_name_en="Stub", last_name_en="Ancestor", notifications_enabled=False
    )
    _login_as(client, owner, family)

    resp = client.get("/people/")

    body = resp.content.decode()
    assert "Tracked Person" in body
    assert "Stub Ancestor" not in body


def test_person_list_show_untracked_requires_editor_role(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    Person.objects.create(
        family=family, first_name_en="Stub", last_name_en="Ancestor", notifications_enabled=False
    )
    _login_as(client, member, family)

    resp = client.get("/people/?show_untracked=1")

    assert "Stub Ancestor" not in resp.content.decode()
    assert resp.context["can_show_untracked"] is False


def test_person_list_show_untracked_reveals_them_for_an_editor(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    Person.objects.create(
        family=family, first_name_en="Stub", last_name_en="Ancestor", notifications_enabled=False
    )
    _login_as(client, owner, family)

    resp = client.get("/people/?show_untracked=1")

    assert "Stub Ancestor" in resp.content.decode()


def test_owner_can_add_a_brand_new_spouse(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person_a = Person.objects.create(family=family, first_name_en="Elchanan", last_name_en="Rokach")
    _login_as(client, owner, family)

    resp = client.post(
        f"/people/{person_a.uuid}/spouse/new/",
        {
            "new_spouse_first_name_en": "Rivka",
            "new_spouse_last_name_en": "Rokach",
            "status": "married",
        },
    )

    assert resp.status_code == 302
    union = Union.objects.get(person_a=person_a)
    assert union.person_b.first_name_en == "Rivka"
    assert union.person_b.family_id == family.id


def test_existing_spouse_choices_exclude_the_same_gender(client, family):
    person_a = Person.objects.create(
        family=family, first_name_en="Elchanan", last_name_en="Rokach", gender=Person.Gender.MALE
    )
    woman = Person.objects.create(
        family=family, first_name_en="Rivka", last_name_en="Rokach", gender=Person.Gender.FEMALE
    )
    man = Person.objects.create(
        family=family, first_name_en="Shloime", last_name_en="Rokach", gender=Person.Gender.MALE
    )
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get(f"/people/{person_a.uuid}/spouse/new/")

    candidate_ids = {p.pk for p in resp.context["form"].fields["existing_spouse"].queryset}
    assert woman.pk in candidate_ids
    assert man.pk not in candidate_ids


def test_owner_can_link_an_existing_person_as_spouse(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person_a = Person.objects.create(family=family, first_name_en="Elchanan", last_name_en="Rokach")
    person_b = Person.objects.create(family=family, first_name_en="Rivka", last_name_en="Rokach")
    _login_as(client, owner, family)

    resp = client.post(
        f"/people/{person_a.uuid}/spouse/new/",
        {"existing_spouse": person_b.pk, "status": "married"},
    )

    assert resp.status_code == 302
    assert Union.objects.filter(person_a=person_a, person_b=person_b).exists()


def test_cannot_submit_both_existing_and_new_spouse(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    person_a = Person.objects.create(family=family, first_name_en="Elchanan", last_name_en="Rokach")
    person_b = Person.objects.create(family=family, first_name_en="Rivka", last_name_en="Rokach")
    _login_as(client, owner, family)

    resp = client.post(
        f"/people/{person_a.uuid}/spouse/new/",
        {
            "existing_spouse": person_b.pk,
            "new_spouse_first_name_en": "Someone",
            "new_spouse_last_name_en": "Else",
            "status": "married",
        },
    )

    assert resp.status_code == 200
    assert not Union.objects.filter(person_a=person_a).exists()


def test_plain_member_cannot_add_a_spouse(client, family):
    member = _member(family, FamilyMembership.Role.MEMBER)
    person_a = Person.objects.create(family=family, first_name_en="Elchanan", last_name_en="Rokach")
    _login_as(client, member, family)

    resp = client.get(f"/people/{person_a.uuid}/spouse/new/")

    assert resp.status_code == 403


@pytest.mark.parametrize(
    ["role", "expected_status", "union_survives"],
    [
        [FamilyMembership.Role.OWNER, 302, False],
        [FamilyMembership.Role.EDITOR, 403, True],
    ],
    ids=[
        "owner can delete a union",
        "editor cannot delete a union",
    ],
)
def test_delete_union_permission_by_role(client, family, role, expected_status, union_survives):
    member = _member(family, role)
    person_a = Person.objects.create(family=family, first_name_en="Elchanan", last_name_en="Rokach")
    person_b = Person.objects.create(family=family, first_name_en="Rivka", last_name_en="Rokach")
    union = Union.objects.create(person_a=person_a, person_b=person_b)
    _login_as(client, member, family)

    resp = client.post(f"/unions/{union.uuid}/delete/")

    assert resp.status_code == expected_status
    assert Union.objects.filter(pk=union.pk).exists() == union_survives


def test_in_laws_family_can_also_edit_the_shared_union(client, family):
    other_family = Family.objects.create(name="Other Family")
    owner_of_other = _member(other_family, FamilyMembership.Role.OWNER)
    person_a = Person.objects.create(family=family, first_name_en="Elchanan", last_name_en="Rokach")
    person_b = Person.objects.create(family=other_family, first_name_en="Rivka", last_name_en="Bernstein")
    union = Union.objects.create(person_a=person_a, person_b=person_b)
    _login_as(client, owner_of_other, other_family)

    resp = client.post(
        f"/unions/{union.uuid}/edit/",
        {
            "status": "married",
            "marriage_hebrew_year": "",
            "marriage_hebrew_month": "",
            "marriage_hebrew_day": "",
        },
    )

    assert resp.status_code == 302
    assert resp.url == f"/people/{person_a.uuid}/"


def test_gregorian_to_hebrew_returns_the_conversion(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    expected = gregorian_to_hebrew(dt.date(1990, 9, 22))

    resp = client.post("/ajax/gregorian-to-hebrew/", {"date": "1990-09-22"})

    assert resp.status_code == 200
    assert resp.json() == {
        "year": expected.year,
        "month": expected.month.value,
        "day": expected.day,
    }


def test_gregorian_to_hebrew_requires_login(client):
    resp = client.post("/ajax/gregorian-to-hebrew/", {"date": "1990-09-22"})

    assert resp.status_code == 302


@pytest.mark.parametrize(
    ["payload"],
    [
        [{"date": "not-a-date"}],
        [{}],
    ],
    ids=[
        "unparseable date string",
        "missing date field entirely",
    ],
)
def test_gregorian_to_hebrew_rejects_bad_input(client, family, payload):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post("/ajax/gregorian-to-hebrew/", payload)

    assert resp.status_code == 400


def test_gregorian_to_hebrew_rejects_get(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get("/ajax/gregorian-to-hebrew/")

    assert resp.status_code == 405


def test_dashboard_never_shows_broadcasts(client, family):
    # Broadcasts are deliberately not part of "Upcoming" - see AGENTS.md -
    # an owner/editor manages them from their own /broadcasts/ page.
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    Broadcast.objects.create(
        family=family, text="Big news", created_by=owner, send_at=timezone.now() + dt.timedelta(days=1)
    )

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"Big news" not in resp.content


def _occurrence(person, code, *, occurrence_date, send_date=None):
    event_type = EventType.objects.get(family=None, code=code)
    return Occurrence.objects.create(
        person=person,
        event_type=event_type,
        hebrew_year=5786,
        occurrence_date=occurrence_date,
        send_date=send_date or occurrence_date,
    )


def test_dashboard_groups_same_date_occurrences_under_one_timeline_entry(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    same_date = timezone.localdate() + dt.timedelta(days=3)
    _occurrence(person, EventType.BuiltinCode.BIRTHDAY, occurrence_date=same_date)
    _occurrence(person, EventType.BuiltinCode.YAHRZEIT, occurrence_date=same_date)

    resp = client.get("/")

    assert len(resp.context["upcoming_groups"]) == 1
    assert len(resp.context["upcoming_groups"][0]["occurrences"]) == 2


def test_dashboard_does_not_group_occurrences_sharing_only_send_date(client, family):
    # A shared send_date doesn't imply a shared occurrence_date - two
    # independent Shabbat/Yom Tov shifts can coincidentally land on the
    # same day - grouping on send_date alone would show one of them
    # under the wrong Hebrew date (see DashboardView's own comment).
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    shared_send_date = timezone.localdate() + dt.timedelta(days=3)
    _occurrence(
        person,
        EventType.BuiltinCode.BIRTHDAY,
        occurrence_date=shared_send_date,
        send_date=shared_send_date,
    )
    _occurrence(
        person,
        EventType.BuiltinCode.YAHRZEIT,
        occurrence_date=shared_send_date + dt.timedelta(days=1),
        send_date=shared_send_date,
    )

    resp = client.get("/")

    assert len(resp.context["upcoming_groups"]) == 2


def test_dashboard_still_shows_an_unsent_occurrence_with_a_past_send_date(client, family):
    # A missed run, or a same-day Shabbat/Yom Tov shift, can leave send_date
    # in the past while is_sent is still False - it's real and about to
    # send, so it shouldn't silently vanish from Upcoming (see
    # send_due_notifications' own send_date__lte for the same reasoning).
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    past_date = timezone.localdate() - dt.timedelta(days=1)
    _occurrence(person, EventType.BuiltinCode.BIRTHDAY, occurrence_date=past_date)

    resp = client.get("/")

    assert len(resp.context["upcoming_groups"]) == 1


def test_dashboard_shows_the_occurrence_date_not_the_send_date(client, family):
    # The timeline's date column used to show send_date next to the
    # Hebrew occurrence_date - when a notification is shifted early for
    # Shabbat/Yom Tov those disagree, and it read as if the event itself
    # had moved. It should show the Gregorian half of occurrence_date.
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    occurrence_date = timezone.localdate() + dt.timedelta(days=3)
    send_date = timezone.localdate()
    _occurrence(
        person,
        EventType.BuiltinCode.BIRTHDAY,
        occurrence_date=occurrence_date,
        send_date=send_date,
    )

    resp = client.get("/")
    content = resp.content.decode()

    assert date_filter(occurrence_date, "l, F j, Y") in content
    assert date_filter(send_date, "l, F j, Y") not in content
    assert "moved up for Shabbat" not in content


def test_dashboard_orders_grouped_occurrences_by_event_type_name(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    same_date = timezone.localdate() + dt.timedelta(days=3)
    # Created in reverse alphabetical order - the view's own explicit
    # .order_by(..., "event_type__name") is what should fix this, not
    # creation/insertion order.
    _occurrence(person, EventType.BuiltinCode.YAHRZEIT, occurrence_date=same_date)
    _occurrence(person, EventType.BuiltinCode.BIRTHDAY, occurrence_date=same_date)

    resp = client.get("/")

    names = [o.event_type.name for o in resp.context["upcoming_groups"][0]["occurrences"]]
    assert names == sorted(names)


def test_dashboard_orders_groups_by_occurrence_date_not_send_date(client, family):
    # Wedding's own notify_days_before=3 means its send_date always lands
    # ~3 days before its occurrence_date - sorting by send_date alone
    # would show a Wedding "3 days from now" ahead of a Birthday
    # "tomorrow", reading as out of chronological order even though each
    # individual date shown (occurrence_date, per the dashboard's own
    # date stamp) is correct on its own.
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    person_a = Person.objects.create(family=family, first_name_en="Sari", last_name_en="Rokach")
    person_b = Person.objects.create(family=family, first_name_en="Moshe", last_name_en="Rokach")
    union = Union.objects.create(person_a=person_a, person_b=person_b)
    wedding_event_type = EventType.objects.get(family=None, code=EventType.BuiltinCode.WEDDING)
    Occurrence.objects.create(
        union=union,
        event_type=wedding_event_type,
        hebrew_year=5786,
        occurrence_date=timezone.localdate() + dt.timedelta(days=3),
        send_date=timezone.localdate(),
    )
    birthday_person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")
    _occurrence(
        birthday_person,
        EventType.BuiltinCode.BIRTHDAY,
        occurrence_date=timezone.localdate() + dt.timedelta(days=1),
        send_date=timezone.localdate() + dt.timedelta(days=1),
    )

    resp = client.get("/")

    groups = resp.context["upcoming_groups"]
    assert [g["occurrence_date"] for g in groups] == sorted(g["occurrence_date"] for g in groups)


def test_dashboard_query_count_does_not_scale_with_candidate_count(client, family):
    # DashboardView used to call channels_for_account() (2-4 queries) once
    # per candidate occurrence, up to 100 of them - see
    # notifications.audience.PreferenceResolver and AGENTS.md's note on
    # this page's query cost. Proven by asserting the same query count for
    # a handful of occurrences and for many more, not just an arbitrary cap.
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    def _add_occurrences(count):
        for i in range(count):
            person = Person.objects.create(family=family, first_name_en=f"Person{i}", last_name_en="Rokach")
            _occurrence(
                person,
                EventType.BuiltinCode.BIRTHDAY,
                occurrence_date=timezone.localdate() + dt.timedelta(days=i),
            )

    _add_occurrences(3)
    with CaptureQueriesContext(connection) as few:
        client.get("/")

    _add_occurrences(15)
    with CaptureQueriesContext(connection) as many:
        client.get("/")

    assert len(many.captured_queries) == len(few.captured_queries)


# --- Cross-tenant access: every editor/owner-gated view that fetches a
# specific record by UUID must 404 for a record outside the current
# workspace, even for a role that could edit/delete that *kind* of
# record in its own family - see tenants.mixins.FamilyScopedMixin and
# AGENTS.md. Person and Union each get a direct negative test here (see
# notifications/tests/test_views.py for Broadcast's own version of this);
# Union's legitimate either-side exception is proven separately by
# test_in_laws_family_can_also_edit_the_shared_union above.


def test_person_delete_404s_for_a_person_outside_your_family(client, two_families):
    family_a, family_b, account_a, _ = two_families
    outsider = Person.objects.create(family=family_b, first_name_en="Not", last_name_en="Yours")
    _login_as(client, account_a, family_a)

    resp = client.post(f"/people/{outsider.uuid}/delete/")

    assert resp.status_code == 404
    assert Person.objects.filter(pk=outsider.pk).exists()


@pytest.mark.parametrize(
    ["url_suffix", "method"],
    [
        ["edit/", "post"],
        ["delete/", "post"],
    ],
    ids=["union update", "union delete"],
)
def test_union_endpoints_404_for_a_union_entirely_outside_your_family(
    client, two_families, url_suffix, method
):
    # Unlike the in-law case (test_in_laws_family_can_also_edit_the_shared_union),
    # neither spouse here is in family_a at all - there's no legitimate
    # either-side access to fall back on.
    family_a, family_b, account_a, _ = two_families
    person_a = Person.objects.create(family=family_b, first_name_en="A", last_name_en="Outsider")
    person_b = Person.objects.create(family=family_b, first_name_en="B", last_name_en="Outsider")
    union = Union.objects.create(person_a=person_a, person_b=person_b)
    _login_as(client, account_a, family_a)

    resp = getattr(client, method)(f"/unions/{union.uuid}/{url_suffix}")

    assert resp.status_code == 404
    assert Union.objects.filter(pk=union.pk).exists()


def test_help_shows_the_owner_by_name_and_email_when_they_have_a_person_record(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    Person.objects.create(family=family, first_name_en="Sheya", last_name_en="Bernstein", account=owner)
    member = _member(family, FamilyMembership.Role.MEMBER)
    _login_as(client, member, family)

    resp = client.get("/help/")

    assert resp.status_code == 200
    assert "Sheya Bernstein" in resp.content.decode()
    assert f"mailto:{owner.email}" in resp.content.decode()


def test_help_joins_multiple_owners_with_commas_and_a_trailing_and(client, family):
    owner_a = Account.objects.create_user(email="a-owner@example.com")
    owner_b = Account.objects.create_user(email="b-owner@example.com")
    owner_c = Account.objects.create_user(email="c-owner@example.com")
    for account, first_name in [(owner_a, "Alpha"), (owner_b, "Bravo"), (owner_c, "Charlie")]:
        FamilyMembership.objects.create(account=account, family=family, role=FamilyMembership.Role.OWNER)
        Person.objects.create(family=family, first_name_en=first_name, last_name_en="Owner", account=account)
    _login_as(client, owner_a, family)

    resp = client.get("/help/")

    text = " ".join(resp.content.decode().split())
    assert (
        '<strong>Alpha Owner</strong> (<a href="mailto:a-owner@example.com">a-owner@example.com</a>), '
        '<strong>Bravo Owner</strong> (<a href="mailto:b-owner@example.com">b-owner@example.com</a>) and '
        "<strong>Charlie Owner</strong>"
    ) in text


def test_help_falls_back_to_the_owner_account_email_with_no_person_record(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get("/help/")

    assert f"mailto:{owner.email}" in resp.content.decode()


def test_help_shows_the_preview_section_to_an_editor_but_not_a_plain_member(client, family):
    editor = _member(family, FamilyMembership.Role.EDITOR)
    _login_as(client, editor, family)
    editor_resp = client.get("/help/")
    assert "Previewing a notification" in editor_resp.content.decode()

    member = _member(family, FamilyMembership.Role.MEMBER)
    _login_as(client, member, family)
    member_resp = client.get("/help/")
    assert "Previewing a notification" not in member_resp.content.decode()


def test_help_shows_nothing_owner_related_with_no_current_family(client):
    account = Account.objects.create_user(email="floating@example.com")
    client.force_login(account)

    resp = client.get("/help/")

    assert resp.status_code == 200
    assert "is managed by" not in resp.content.decode()
