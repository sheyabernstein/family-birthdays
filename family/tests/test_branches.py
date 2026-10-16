import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from family.branches import BRANCH_COLORS, branch_color, compute_branches
from family.models import Person, Union

pytestmark = pytest.mark.django_db


def _person(family, **kwargs):
    kwargs.setdefault("first_name_he", "פלוני")
    return Person.objects.create(family=family, **kwargs)


def test_compute_branches_returns_nothing_without_a_tree_root(family):
    _person(family, first_name_en="Someone")

    assert compute_branches(family) == {}


def test_compute_branches_excludes_the_tree_root_itself(family):
    root = _person(family, first_name_en="Root")
    family.tree_root = root
    family.save(update_fields=["tree_root"])

    branches = compute_branches(family)

    assert root.id not in branches


def test_compute_branches_color_is_stable_when_a_new_earlier_sorting_child_is_added_later(family):
    # A later-added child named to sort alphabetically before an
    # existing one must not reshuffle the existing child's own branch
    # index (and so its export color) - see compute_branches's own
    # docstring on why this orders by pk, not name.
    root = _person(family, first_name_en="Root")
    existing_child = _person(family, first_name_en="Zev", father=root)
    family.tree_root = root
    family.save(update_fields=["tree_root"])
    before = compute_branches(family)
    existing_index = before[existing_child.id]

    _person(family, first_name_en="Aaron", father=root)
    after = compute_branches(family)

    assert after[existing_child.id] == existing_index


def test_compute_branches_assigns_each_child_its_own_branch(family):
    root = _person(family, first_name_en="Root")
    child_a = _person(family, first_name_en="ChildA", father=root)
    child_b = _person(family, first_name_en="ChildB", father=root)
    family.tree_root = root
    family.save(update_fields=["tree_root"])

    branches = compute_branches(family)

    assert branches[child_a.id] != branches[child_b.id]


def test_compute_branches_assigns_blood_descendants_to_their_ancestors_branch(family):
    root = _person(family, first_name_en="Root")
    child = _person(family, first_name_en="Child", father=root)
    grandchild = _person(family, first_name_en="Grandchild", father=child)
    family.tree_root = root
    family.save(update_fields=["tree_root"])

    branches = compute_branches(family)

    assert branches[grandchild.id] == branches[child.id]


def test_compute_branches_gives_an_untracked_spouse_their_married_in_branch(family):
    root = _person(family, first_name_en="Root")
    child = _person(family, first_name_en="Child", father=root, gender=Person.Gender.FEMALE)
    spouse = _person(family, first_name_en="Spouse", gender=Person.Gender.MALE)
    family.tree_root = root
    family.save(update_fields=["tree_root"])
    Union.objects.create(person_a=child, person_b=spouse, status=Union.Status.MARRIED)

    branches = compute_branches(family)

    assert branches[spouse.id] == branches[child.id]


def test_compute_branches_spouse_inheritance_stops_at_one_hop(family):
    root = _person(family, first_name_en="Root")
    child = _person(family, first_name_en="Child", father=root, gender=Person.Gender.FEMALE)
    in_law_a = _person(family, first_name_en="InLawA", gender=Person.Gender.MALE)
    in_law_b = _person(family, first_name_en="InLawB", gender=Person.Gender.FEMALE)
    family.tree_root = root
    family.save(update_fields=["tree_root"])
    Union.objects.create(person_a=child, person_b=in_law_a, status=Union.Status.MARRIED)
    # in_law_b has no blood or direct-marriage path to root - only a
    # second marriage hop, through in_law_a, which doesn't count (see
    # compute_branches's own docstring).
    Union.objects.create(person_a=in_law_a, person_b=in_law_b, status=Union.Status.MARRIED)

    branches = compute_branches(family)

    assert in_law_a.id in branches
    assert in_law_b.id not in branches


def test_compute_branches_blood_wins_over_a_spouses_branch(family):
    root = _person(family, first_name_en="Root")
    child_a = _person(family, first_name_en="ChildA", father=root, gender=Person.Gender.MALE)
    child_b = _person(family, first_name_en="ChildB", father=root, gender=Person.Gender.FEMALE)
    grandchild = _person(family, first_name_en="Grandchild", father=child_a, gender=Person.Gender.MALE)
    Union.objects.create(person_a=grandchild, person_b=child_b, status=Union.Status.MARRIED)
    family.tree_root = root
    family.save(update_fields=["tree_root"])

    branches = compute_branches(family)

    # grandchild is blood-descended from child_a, not child_b, even
    # though married to child_b - blood wins (see compute_branches).
    assert branches[grandchild.id] == branches[child_a.id]


def test_compute_branches_leaves_an_unreachable_person_uncolored(family):
    root = _person(family, first_name_en="Root")
    unrelated = _person(family, first_name_en="Unrelated")
    family.tree_root = root
    family.save(update_fields=["tree_root"])

    branches = compute_branches(family)

    assert unrelated.id not in branches


def test_branch_color_cycles_past_the_palette_length():
    assert branch_color(0) == branch_color(len(BRANCH_COLORS))


def test_compute_branches_query_count_does_not_scale_with_generation_depth(family):
    # Two queries total (people, unions), regardless of how many
    # generations deep the tree goes - see compute_branches's own
    # docstring on why this matters for an export someone clicks
    # repeatedly.
    root = _person(family, first_name_en="Root")
    parent = root
    for i in range(10):
        parent = _person(family, first_name_en=f"Gen{i}", father=parent)
    family.tree_root = root
    family.save(update_fields=["tree_root"])

    with CaptureQueriesContext(connection) as captured:
        compute_branches(family)

    assert len(captured.captured_queries) == 2
