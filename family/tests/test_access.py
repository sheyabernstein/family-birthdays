import pytest

from accounts.models import Account
from family.access import can_see_birth_year, person_is_visible, union_is_visible
from family.models import Person, Union

pytestmark = pytest.mark.django_db


def test_person_in_another_family_is_not_visible_by_default(two_families):
    family_a, family_b, _, _ = two_families
    person_b = Person.objects.create(family=family_b, first_name_en="Other", last_name_en="Family")

    assert person_is_visible(person_b, family_a) is False


def test_person_is_visible_to_their_own_family(two_families):
    family_a, _, _, _ = two_families
    person_a = Person.objects.create(family=family_a, first_name_en="Mine", last_name_en="Family")

    assert person_is_visible(person_a, family_a) is True


def test_in_law_is_visible_through_a_union(two_families):
    family_a, family_b, _, _ = two_families
    person_a = Person.objects.create(family=family_a, first_name_en="Mine", last_name_en="Family")
    person_b = Person.objects.create(family=family_b, first_name_en="Married", last_name_en="In")
    Union.objects.create(person_a=person_a, person_b=person_b)

    assert person_is_visible(person_b, family_a) is True
    assert union_is_visible(Union.objects.get(person_a=person_a), family_a) is True
    assert union_is_visible(Union.objects.get(person_a=person_a), family_b) is True


def test_can_see_birth_year_is_always_true_for_an_editor(family):
    someone_else = Person.objects.create(family=family, first_name_en="Someone", last_name_en="Else")

    assert can_see_birth_year(someone_else, can_edit=True, viewer_account_id=999) is True


def test_can_see_birth_year_is_false_for_a_member_viewing_someone_else(family):
    someone_else = Person.objects.create(family=family, first_name_en="Someone", last_name_en="Else")

    assert can_see_birth_year(someone_else, can_edit=False, viewer_account_id=999) is False


def test_can_see_birth_year_is_true_for_a_member_viewing_their_own_record(family):
    account = Account.objects.create_user(email="me@example.com")
    myself = Person.objects.create(family=family, first_name_en="My", last_name_en="Self", account=account)

    assert can_see_birth_year(myself, can_edit=False, viewer_account_id=account.id) is True


def test_can_see_birth_year_is_false_for_an_unlinked_viewer(family):
    someone_else = Person.objects.create(family=family, first_name_en="Someone", last_name_en="Else")

    assert can_see_birth_year(someone_else, can_edit=False, viewer_account_id=None) is False
