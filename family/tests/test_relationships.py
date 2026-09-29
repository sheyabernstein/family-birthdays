import pytest

from family.models import Person, Union
from family.relationships import (
    is_ancestor,
    is_descendant,
    is_direct_family,
    is_immediate_family,
    memoize_by_person,
)

pytestmark = pytest.mark.django_db


def test_parent_and_child_are_immediate_family(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    child = Person.objects.create(family=family, first_name_en="Child", last_name_en="Person", father=parent)

    assert is_immediate_family(parent, child) is True
    assert is_immediate_family(child, parent) is True


def test_siblings_sharing_one_parent_are_immediate_family(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person", father=parent)
    b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person", father=parent)

    assert is_immediate_family(a, b) is True


@pytest.mark.parametrize(
    ["status", "expected"],
    [
        [Union.Status.MARRIED, True],
        [Union.Status.DIVORCED, False],
    ],
    ids=[
        "married spouses are immediate family",
        "unmarried partners are not immediate family",
    ],
)
def test_spouse_status_determines_immediate_family(family, status, expected):
    a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person")
    b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person")
    Union.objects.create(person_a=a, person_b=b, status=status)

    assert is_immediate_family(a, b) is expected


def test_grandparent_is_not_immediate_family(family):
    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", father=grandparent
    )
    grandchild = Person.objects.create(
        family=family, first_name_en="Child", last_name_en="Person", father=parent
    )

    assert is_immediate_family(grandparent, grandchild) is False


def test_cousin_is_not_immediate_family(family):
    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent_a = Person.objects.create(
        family=family, first_name_en="ParentA", last_name_en="Person", father=grandparent
    )
    parent_b = Person.objects.create(
        family=family, first_name_en="ParentB", last_name_en="Person", father=grandparent
    )
    cousin_a = Person.objects.create(
        family=family, first_name_en="CousinA", last_name_en="Person", father=parent_a
    )
    cousin_b = Person.objects.create(
        family=family, first_name_en="CousinB", last_name_en="Person", father=parent_b
    )

    assert is_immediate_family(cousin_a, cousin_b) is False


def test_parent_is_an_ancestor(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    child = Person.objects.create(family=family, first_name_en="Child", last_name_en="Person", father=parent)

    assert is_ancestor(child, parent) is True
    assert is_ancestor(parent, child) is False


def test_grandparent_via_mother_line_is_an_ancestor(family):
    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", mother=grandparent
    )
    grandchild = Person.objects.create(
        family=family, first_name_en="Child", last_name_en="Person", mother=parent
    )

    assert is_ancestor(grandchild, grandparent) is True


def test_sibling_is_not_an_ancestor(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person", father=parent)
    b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person", father=parent)

    assert is_ancestor(a, b) is False


def test_person_is_not_their_own_ancestor(family):
    person = Person.objects.create(family=family, first_name_en="Solo", last_name_en="Person")

    assert is_ancestor(person, person) is False


def test_child_is_a_descendant(family):
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    child = Person.objects.create(family=family, first_name_en="Child", last_name_en="Person", father=parent)

    assert is_descendant(parent, child) is True
    assert is_descendant(child, parent) is False


def test_great_grandchild_is_a_descendant(family):
    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", father=grandparent
    )
    grandchild = Person.objects.create(
        family=family, first_name_en="Grandchild", last_name_en="Person", father=parent
    )

    assert is_descendant(grandparent, grandchild) is True


def test_person_is_not_their_own_descendant(family):
    person = Person.objects.create(family=family, first_name_en="Solo", last_name_en="Person")

    assert is_descendant(person, person) is False


# --- is_direct_family unions immediate family, the whole ancestor/
# descendant line, and the same again through one marriage hop - see
# that function's own docstring and AGENTS.md. These trace through the
# exact worked examples from the design discussion: a grandmother's
# yahrzeit reaches every descendant (any depth) and their spouses, but a
# spouse's grandmother's yahrzeit only reaches the couple and their own
# shared descendants, not the viewer's own separate blood relatives. ---


def test_direct_family_reaches_a_spouse_of_a_descendant(family):
    # "my grandmother is sent to ... my spouse" - my spouse isn't my
    # grandmother's own descendant, but *I* am, and my spouse merges
    # with me through our own marriage.
    grandmother = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    me = Person.objects.create(family=family, first_name_en="Me", last_name_en="Person", father=grandmother)
    spouse = Person.objects.create(family=family, first_name_en="Spouse", last_name_en="Person")
    Union.objects.create(person_a=me, person_b=spouse, status=Union.Status.MARRIED)

    assert is_direct_family(spouse, grandmother) is True


def test_direct_family_reaches_a_nephews_spouse_down_the_chain(family):
    # "... and my nephews/nieces too down the chain" - a niece is a
    # blood descendant of the same grandmother (via a sibling), and her
    # own husband merges with her the same way any spouse does.
    grandmother = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    sibling = Person.objects.create(
        family=family, first_name_en="Sibling", last_name_en="Person", father=grandmother
    )
    niece = Person.objects.create(family=family, first_name_en="Niece", last_name_en="Person", father=sibling)
    niece_husband = Person.objects.create(family=family, first_name_en="Husband", last_name_en="Person")
    Union.objects.create(person_a=niece, person_b=niece_husband, status=Union.Status.MARRIED)

    assert is_direct_family(niece_husband, grandmother) is True


def test_direct_family_reaches_a_spouses_grandmother_through_marriage(family):
    # "my spouse's grandmother is sent to me/my spouse" - the in-law
    # case: I'm not my spouse's grandmother's own descendant, but my
    # spouse is, and merges with me.
    spouses_grandmother = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    spouse = Person.objects.create(
        family=family, first_name_en="Spouse", last_name_en="Person", father=spouses_grandmother
    )
    me = Person.objects.create(family=family, first_name_en="Me", last_name_en="Person")
    Union.objects.create(person_a=me, person_b=spouse, status=Union.Status.MARRIED)

    assert is_direct_family(me, spouses_grandmother) is True


def test_direct_family_reaches_shared_descendants_of_an_in_laws_grandmother(family):
    # "... and my/our descendants" - our shared child is a blood
    # descendant of my spouse's grandmother even without any marriage
    # merge at all.
    spouses_grandmother = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    spouse = Person.objects.create(
        family=family, first_name_en="Spouse", last_name_en="Person", father=spouses_grandmother
    )
    me = Person.objects.create(family=family, first_name_en="Me", last_name_en="Person")
    our_child = Person.objects.create(
        family=family, first_name_en="Child", last_name_en="Person", father=me, mother=spouse
    )

    assert is_direct_family(our_child, spouses_grandmother) is True


def test_direct_family_does_not_reach_my_own_relatives_for_a_spouses_grandmother(family):
    # The asymmetry that matters: my own sibling has no blood relation to
    # my spouse's grandmother, and didn't marry into that family either -
    # only *I* did, through my own marriage.
    spouses_grandmother = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    spouse = Person.objects.create(
        family=family, first_name_en="Spouse", last_name_en="Person", father=spouses_grandmother
    )
    me = Person.objects.create(family=family, first_name_en="Me", last_name_en="Person")
    Union.objects.create(person_a=me, person_b=spouse, status=Union.Status.MARRIED)
    my_parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    me.father = my_parent
    me.save(update_fields=["father"])
    my_sibling = Person.objects.create(
        family=family, first_name_en="Sibling", last_name_en="Person", father=my_parent
    )

    assert is_direct_family(my_sibling, spouses_grandmother) is False


def test_memoize_by_person_returns_the_computed_value(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    cached = memoize_by_person(lambda p: {p.id})

    assert cached(person) == {person.id}


def test_memoize_by_person_only_computes_once_per_person(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    calls = []
    cached = memoize_by_person(lambda p: calls.append(p.id) or {p.id})

    cached(person)
    cached(person)

    assert calls == [person.id]


def test_memoize_by_person_computes_separately_per_person(family):
    a = Person.objects.create(family=family, first_name_en="A", last_name_en="Person")
    b = Person.objects.create(family=family, first_name_en="B", last_name_en="Person")
    cached = memoize_by_person(lambda p: {p.id})

    assert cached(a) == {a.id}
    assert cached(b) == {b.id}
