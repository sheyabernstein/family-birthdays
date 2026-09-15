import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from tenants.models import Family

pytestmark = pytest.mark.django_db


@override_settings(EMAIL_SENDING_DOMAIN="family-tree.example")
def test_sender_email_is_derived_from_slug_and_the_sending_domain():
    family = Family.objects.create(name="Rokach Family")

    assert family.sender_email == f"noreply-{family.slug}@family-tree.example"


def test_sender_email_does_not_change_when_the_family_is_renamed():
    family = Family.objects.create(name="Rokach Family")
    original_sender_email = family.sender_email

    family.name = "Bernstein Family"
    family.save()

    assert family.sender_email == original_sender_email


@pytest.mark.parametrize(
    ["sms_sender_id", "is_valid"],
    [
        ["RokachFam", True],
        ["", True],
        ["Rokach Fam", False],
        ["Rokach-Fam", False],
        ["RokachFamily1", False],
    ],
    ids=[
        "alphanumeric is valid",
        "blank is valid",
        "spaces are rejected",
        "hyphens are rejected",
        "over 10 characters is rejected",
    ],
)
def test_sms_sender_id_validation(sms_sender_id, is_valid):
    family = Family(name="Test Family", sms_sender_id=sms_sender_id)

    if is_valid:
        family.full_clean(exclude=["slug"])
    else:
        with pytest.raises(ValidationError):
            family.full_clean(exclude=["slug"])
