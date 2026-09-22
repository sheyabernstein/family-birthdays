import pytest

from accounts.models import Account
from family.forms import PersonForm
from family.models import Person
from tenants.models import Family

pytestmark = pytest.mark.django_db


def test_phone_field_renders_as_a_tel_input_for_the_intl_tel_input_widget(family):
    form = PersonForm(family=family)
    assert form.fields["phone"].widget.input_type == "tel"


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
