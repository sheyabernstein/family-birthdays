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


@override_settings(EMAIL_SENDING_DOMAIN="family-tree.example")
def test_sender_email_uses_the_familys_own_domain_override_when_set():
    family = Family.objects.create(name="Rokach Family", email_sending_domain="rokach.example")

    assert family.sender_email == f"noreply-{family.slug}@rokach.example"


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


@override_settings(SITE_BASE_URL="https://default.example.com")
def test_resolved_base_url_falls_back_to_the_global_setting_when_blank():
    family = Family.objects.create(name="Test Family")

    assert family.resolved_base_url == "https://default.example.com"


def test_resolved_base_url_uses_the_familys_own_override_when_set():
    family = Family.objects.create(name="Test Family", base_url="https://family.example.com")

    assert family.resolved_base_url == "https://family.example.com"


def test_resolved_base_url_strips_a_trailing_slash():
    family = Family.objects.create(name="Test Family", base_url="https://family.example.com/")

    assert family.resolved_base_url == "https://family.example.com"


@pytest.mark.parametrize(
    ["base_url", "expected_host"],
    [
        ["", None],
        ["https://family.example.com", "family.example.com"],
        ["https://FAMILY.example.com", "family.example.com"],
        ["https://family.example.com:443", "family.example.com"],
        ["http://family.example.com:80", "family.example.com"],
        ["https://family.example.com:8443", "family.example.com:8443"],
        ["https://café.example", "xn--caf-dma.example"],
    ],
    ids=[
        "blank has no host",
        "plain override's host",
        "lowercased",
        "explicit default https port dropped",
        "explicit default http port dropped",
        "non-default port kept",
        "unicode host encoded to punycode, matching what a browser actually sends",
    ],
)
def test_base_url_host_normalizes_like_a_real_browsers_host_header(base_url, expected_host):
    family = Family.objects.create(name="Test Family", base_url=base_url)

    assert family.base_url_host == expected_host


def test_base_url_host_fails_safe_on_an_unparseable_base_url():
    """URLValidator already rejects every input confirmed to trigger
    this at the admin layer, so .update() bypasses it here to exercise
    the fail-safe path directly rather than trust that holds forever."""
    family = Family.objects.create(name="Test Family")
    Family.objects.filter(pk=family.pk).update(base_url="https://[malformed")
    family.refresh_from_db()

    assert family.base_url_host is None
