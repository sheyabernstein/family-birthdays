import pytest

from accounts.models import Account
from family.access import (
    can_see_birth_year,
    person_is_visible,
    person_is_visible_to,
    union_is_visible,
    visible_people_for_tree,
)
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


def test_person_is_visible_to_defaults_to_everyone(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")

    assert person_is_visible_to(person, viewer=None, can_edit=False) is True


def test_person_is_visible_to_nobody_is_only_true_for_an_editor(family):
    person = Person.objects.create(
        family=family, first_name_en="Private", last_name_en="Person", visibility=Person.Visibility.NOBODY
    )

    assert person_is_visible_to(person, viewer=None, can_edit=False) is False
    assert person_is_visible_to(person, viewer=None, can_edit=True) is True


def test_person_is_visible_to_immediate_family_reaches_a_parent_not_a_stranger(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    child = Person.objects.create(
        family=family,
        first_name_en="Kid",
        last_name_en="Person",
        father=parent,
        visibility=Person.Visibility.IMMEDIATE_FAMILY,
    )
    stranger = Person.objects.create(family=family, first_name_en="Stranger", last_name_en="Person")

    assert person_is_visible_to(child, viewer=parent, can_edit=False) is True
    assert person_is_visible_to(child, viewer=stranger, can_edit=False) is False


def test_visible_people_for_tree_cuts_an_invisible_person_and_their_descendants(family):
    root = Person.objects.create(family=family, first_name_en="Root", last_name_en="Person")
    private_child = Person.objects.create(
        family=family,
        first_name_en="Private",
        last_name_en="Person",
        father=root,
        visibility=Person.Visibility.NOBODY,
    )
    grandchild = Person.objects.create(
        family=family, first_name_en="Grandchild", last_name_en="Person", father=private_child
    )
    people = [root, private_child, grandchild]

    visible = visible_people_for_tree(people, viewer=None, can_edit=False, keep_id=root.id)

    assert visible == [root]


def test_visible_people_for_tree_never_cuts_its_own_subject(family):
    """The tree's own subject already passed get_object()'s own visibility
    check - an invisible *ancestor* still cuts everything between them
    and the subject (the "cut branch" tradeoff - see /help/), but must
    never also un-render the subject's own page."""
    private_grandparent = Person.objects.create(
        family=family,
        first_name_en="Private",
        last_name_en="Person",
        visibility=Person.Visibility.NOBODY,
    )
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", father=private_grandparent
    )
    subject = Person.objects.create(family=family, first_name_en="Me", last_name_en="Person", father=parent)
    people = [private_grandparent, parent, subject]

    visible = visible_people_for_tree(people, viewer=None, can_edit=False, keep_id=subject.id)

    assert visible == [subject]


def test_visible_people_for_tree_is_a_no_op_for_an_editor(family):
    private_person = Person.objects.create(
        family=family, first_name_en="Private", last_name_en="Person", visibility=Person.Visibility.NOBODY
    )
    people = [private_person]

    visible = visible_people_for_tree(people, viewer=None, can_edit=True, keep_id=999)

    assert visible == people
