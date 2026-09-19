import pytest

from accounts.models import Account
from family.models import Person
from tenants.models import Family

pytestmark = pytest.mark.django_db


def test_linked_person_is_none_when_the_account_has_no_person(family):
    account = Account.objects.create_user(email="test@example.com")

    assert account.linked_person is None


def test_linked_person_returns_the_one_linked_person(family):
    account = Account.objects.create_user(email="test@example.com")
    person = Person.objects.create(
        family=family, first_name_en="Test", last_name_en="Person", account=account
    )

    assert account.linked_person == person


def test_linked_person_is_none_when_linked_to_more_than_one_person(family):
    # Married into two families (AGENTS.md) - ambiguous, fails closed.
    other_family = Family.objects.create(name="Other Family")
    account = Account.objects.create_user(email="test@example.com")
    Person.objects.create(family=family, first_name_en="Test", last_name_en="Person", account=account)
    Person.objects.create(family=other_family, first_name_en="Test", last_name_en="Person", account=account)

    assert account.linked_person is None


def test_display_name_is_blank_without_a_linked_person():
    account = Account.objects.create_user(email="test@example.com")

    assert account.display_name == ""


def test_display_name_uses_the_linked_persons_own_display_name(family):
    account = Account.objects.create_user(email="test@example.com")
    Person.objects.create(
        family=family, first_name_en="Robert", last_name_en="Smith", nickname="Bobby", account=account
    )

    assert account.display_name == "Bobby"


def test_str_falls_back_through_display_name_email_phone(family):
    account = Account.objects.create_user(email="test@example.com")
    Person.objects.create(family=family, first_name_en="Robert", last_name_en="Smith", account=account)

    assert str(account) == "Robert Smith"

    unlinked = Account.objects.create_user(email="unlinked@example.com")
    assert str(unlinked) == "unlinked@example.com"


def test_person_in_family_resolves_the_right_one_even_when_linked_to_two_families(family):
    # Never ambiguous within one family, unlike linked_person.
    other_family = Family.objects.create(name="Other Family")
    account = Account.objects.create_user(email="test@example.com")
    here = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person", account=account)
    Person.objects.create(family=other_family, first_name_en="Test", last_name_en="Person", account=account)

    assert account.person_in_family(family.id) == here
    assert account.linked_person is None


def test_person_in_family_reuses_a_prefetch_instead_of_requerying(family, django_assert_num_queries):
    account = Account.objects.create_user(email="test@example.com")
    Person.objects.create(family=family, first_name_en="Test", last_name_en="Person", account=account)

    # .get() already resolves the prefetch, so both calls below are free.
    prefetched = Account.objects.prefetch_related("people").get(pk=account.pk)
    with django_assert_num_queries(0):
        prefetched.person_in_family(family.id)
        prefetched.person_in_family(family.id)


def test_find_by_identifier_matches_email_case_insensitively():
    account = Account.objects.create_user(email="test@example.com")

    assert Account.find_by_identifier("Test@Example.com") == account


@pytest.mark.parametrize(
    ["typed"],
    [
        ["+15551234567"],
        ["+1 555-123-4567"],
        ["+1 (555) 123-4567"],
        ["+1 555 123 4567"],
        [" +15551234567 "],
    ],
    ids=[
        "already clean E.164",
        "hyphens",
        "parens and a hyphen",
        "spaces",
        "surrounding whitespace",
    ],
)
def test_find_by_identifier_normalizes_phone_punctuation(typed):
    account = Account.objects.create_user(phone="+15551234567")

    assert Account.find_by_identifier(typed) == account


def test_find_by_identifier_returns_none_for_a_phone_missing_its_country_code():
    # The stored phone is always E.164 (a leading "+") - normalizing
    # punctuation doesn't invent a country code that was never typed.
    Account.objects.create_user(phone="+15551234567")

    assert Account.find_by_identifier("555-123-4567") is None
