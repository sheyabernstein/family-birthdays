import pytest

from accounts.models import Account
from family.forms import PersonForm
from family.models import Person
from tenants.models import Family, FamilyMembership

pytestmark = pytest.mark.django_db


def test_phone_field_renders_as_a_tel_input_for_the_intl_tel_input_widget(family):
    form = PersonForm(family=family)
    assert form.fields["phone"].widget.input_type == "tel"


def test_visibility_is_optional_and_defaults_to_everyone(family):
    form = PersonForm(
        data={
            "first_name_he": "חדש",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
        family=family,
    )

    assert form.is_valid(), form.errors
    person = form.save()

    assert person.visibility == Person.Visibility.EVERYONE


def test_first_name_he_is_required_but_first_name_en_is_not(family):
    form = PersonForm(data={"first_name_en": "New", "last_name_en": "Person"}, family=family)

    assert not form.is_valid()
    assert "first_name_he" in form.errors
    assert "first_name_en" not in form.errors


def test_a_person_can_be_added_with_only_a_hebrew_first_name(family):
    form = PersonForm(
        data={
            "first_name_he": "חדש",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
        family=family,
    )

    assert form.is_valid(), form.errors


def test_matching_an_account_already_tracked_in_another_family_is_rejected(family):
    # Without this check, save()'s "already linked" branch would overwrite
    # the other family's real login email/phone the next time this family
    # edits it, and this family would get instant access to a stranger's
    # account with no consent from them.
    other_family = Family.objects.create(name="Other Family")
    account = Account.objects.create_user(email="shared@example.com")
    Person.objects.create(
        family=other_family, first_name_en="Already", last_name_en="Tracked", account=account
    )

    form = PersonForm(
        data={
            "first_name_en": "New",
            "last_name_en": "Person",
            "email": "shared@example.com",
            "family_role": "member",
        },
        family=family,
    )

    assert not form.is_valid()
    assert "email" in form.errors
    assert form._matched_account is None


def test_matching_a_fresh_account_still_auto_links_and_can_grant_a_role(family):
    form = PersonForm(
        data={
            "first_name_en": "New",
            "last_name_en": "Person",
            "first_name_he": "חדש",
            "email": "brand-new@example.com",
            "family_role": "member",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
        family=family,
    )

    assert form.is_valid(), form.errors
    person = form.save()

    assert person.account is not None
    assert person.account.email == "brand-new@example.com"


def test_notification_toggles_apply_to_a_newly_created_account(family):
    form = PersonForm(
        data={
            "first_name_en": "New",
            "last_name_en": "Person",
            "first_name_he": "חדש",
            "email": "toggles-new@example.com",
            "family_role": "member",
            "account_email_notifications_enabled": True,
            "account_sms_notifications_enabled": False,
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
        family=family,
    )

    assert form.is_valid(), form.errors
    person = form.save()

    assert person.account.email_notifications_enabled is True
    assert person.account.sms_notifications_enabled is False


def test_notification_toggles_do_not_overwrite_a_newly_matched_existing_account(family):
    """Regression guard: clean() resolves _matched_account invisibly,
    so the submitted checkboxes are only ever showing their plain field
    defaults at that point, not this account's real settings - save()
    must leave them untouched rather than silently reversing a real
    opt-out with a default nobody actually chose."""
    existing = Account.objects.create_user(
        email="matched-existing@example.com",
        email_notifications_enabled=False,
        sms_notifications_enabled=False,
    )

    form = PersonForm(
        data={
            "first_name_en": "New",
            "last_name_en": "Person",
            "first_name_he": "חדש",
            "email": "matched-existing@example.com",
            "family_role": "member",
            "account_email_notifications_enabled": True,
            "account_sms_notifications_enabled": True,
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
        family=family,
    )

    assert form.is_valid(), form.errors
    form.save()

    existing.refresh_from_db()
    assert existing.email_notifications_enabled is False
    assert existing.sms_notifications_enabled is False


def test_notification_toggles_prefill_from_an_already_linked_account(family):
    account = Account.objects.create_user(
        email="already-linked@example.com",
        email_notifications_enabled=False,
        sms_notifications_enabled=True,
    )
    person = Person.objects.create(
        family=family, first_name_en="Linked", last_name_en="Person", account=account
    )

    form = PersonForm(instance=person, family=family)

    assert form.fields["account_email_notifications_enabled"].initial is False
    assert form.fields["account_sms_notifications_enabled"].initial is True


def test_notification_toggles_apply_to_an_already_linked_account_on_edit(family):
    account = Account.objects.create_user(email="editing@example.com")
    person = Person.objects.create(
        family=family, first_name_en="Linked", last_name_en="Person", account=account
    )

    form = PersonForm(
        instance=person,
        data={
            "first_name_en": "Linked",
            "last_name_en": "Person",
            "first_name_he": "חדש",
            "email": "editing@example.com",
            "family_role": "member",
            "account_email_notifications_enabled": False,
            "account_sms_notifications_enabled": False,
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
        family=family,
    )

    assert form.is_valid(), form.errors
    form.save()

    account.refresh_from_db()
    assert account.email_notifications_enabled is False
    assert account.sms_notifications_enabled is False


def test_account_in_other_families_is_false_for_a_single_family_account(family):
    account = Account.objects.create_user(email="single-family@example.com")
    person = Person.objects.create(
        family=family, first_name_en="Single", last_name_en="Family", account=account
    )

    form = PersonForm(instance=person, family=family)

    assert form.account_in_other_families is False


def test_account_in_other_families_is_true_when_editing_an_already_multi_family_account(family):
    # No Person row in the other family at all - a bare FamilyMembership
    # (e.g. an in-law given access without being added to that family's
    # own tree) is exactly the case PersonForm's own cross_family_
    # conflict check (Person rows only) doesn't catch.
    other_family = Family.objects.create(name="Other Family")
    account = Account.objects.create_user(email="multi-membership@example.com")
    FamilyMembership.objects.create(account=account, family=other_family)
    person = Person.objects.create(
        family=family, first_name_en="Multi", last_name_en="Family", account=account
    )

    form = PersonForm(instance=person, family=family)

    assert form.account_in_other_families is True


def test_account_in_other_families_is_true_when_matching_an_existing_multi_membership_account(family):
    other_family = Family.objects.create(name="Other Family")
    account = Account.objects.create_user(email="matched-multi@example.com")
    FamilyMembership.objects.create(account=account, family=other_family)

    form = PersonForm(
        data={
            "first_name_en": "New",
            "last_name_en": "Person",
            "first_name_he": "חדש",
            "email": "matched-multi@example.com",
            "family_role": "member",
            "yahrzeit_adar_observance": "adar_ii",
            "yahrzeit_day30_observance": "start_of_next_month",
        },
        family=family,
    )

    assert form.is_valid(), form.errors
    assert form.account_in_other_families is True
