"""Builds the recursive husband/wife/children listing a kvitel is traditionally written as.

Modeled directly on the real family-history spreadsheet's own "Kvitel"
sheet: one block per couple (husband first by gender, unprefixed; wife
next, prefixed "ז'"; then their own unmarried/childless children,
prefixed "ב'", bare by first name), followed by a nested block for each
child who's themselves gone on to marry or have kids - confirmed against
that sheet's actual rows and border lines, not designed from scratch.
"ז'"/"ב'" are the real abbreviations that sheet uses (spouse/
son-or-daughter), not a deceased/living marker. A kvitel is for the
living, so anyone deceased is skipped entirely, at every level - a couple
with no living members at all produces no lines of their own, but still
recurses into any living child of theirs who's become a parent in turn.

Two queries total (people, unions) for the core walk, then a pure
in-memory graph walk - same reasoning as family.branches.compute_branches
(not shared code - the two walks differ too much for that to pay off). A
plain member additionally costs a small, viewer-bounded number of queries
for Person.visibility pruning (see build_kvitel) - never per candidate.
"""

import dataclasses
from collections import defaultdict

from django.db import models

from family.access import PersonVisibility
from family.models import Person, Union, _matronymic_label


@dataclasses.dataclass(frozen=True)
class KvitelLine:
    """One rendered line, plus whether a block-separating border follows it.

    role is "husband"/"wife"/"child" for a caller that wants to style
    them differently - the ordering and block grouping are already baked
    into the list itself by build_kvitel.
    """

    text: str
    role: str
    ends_block: bool = False


@dataclasses.dataclass(frozen=True)
class _PersonRow:
    first_name_he: str
    gender: str
    mother_id: int | None
    is_living: bool
    dob_gregorian: object


def has_living_spouse_or_children(person: Person) -> bool:
    """Whether person's own kvitel page would show anything beyond just their own name.

    build_kvitel always emits at least the subject's own line (see its
    docstring's "husband" case for someone with no spouse), so it isn't
    a usable proxy for "is there a kvitel worth linking to" - this is
    the actual check for that, used to gate the detail page's own
    "Kvitel" button.
    """
    # Spouse must be living AND tracked in this same family - a marriage
    # to an in-law tracked only elsewhere doesn't count, matching
    # build_kvitel's own same-family-only scoping (see its docstring).
    has_living_spouse = Union.objects.filter(
        models.Q(person_a=person, person_b__is_living=True, person_b__family=person.family_id)
        | models.Q(person_b=person, person_a__is_living=True, person_a__family=person.family_id),
        status=Union.Status.MARRIED,
    ).exists()
    if has_living_spouse:
        return True
    return Person.objects.filter(models.Q(father=person) | models.Q(mother=person), is_living=True).exists()


def _descendant_ids(start_id: int, children_of: dict[int, list[int]]) -> set[int]:
    """Blood descendants of start_id, via the already-built children_of adjacency - no further queries."""
    seen: set[int] = set()
    frontier = [start_id]
    while frontier:
        next_frontier = [
            child_id
            for person_id in frontier
            for child_id in children_of.get(person_id, [])
            if child_id not in seen
        ]
        seen.update(next_frontier)
        frontier = next_frontier
    return seen


def build_kvitel(root: Person, *, viewer: Person | None, can_edit: bool) -> list[KvitelLine]:
    """Recursively lists root's own living spouse/children, then each married/with-children child's own block.

    Husband lists first, by gender - not by which of the two actually
    descends in blood from root's own family, matching the real sheet (a
    married-in son-in-law lists before his wife, the actual blood
    daughter). Scoped to root's own family only - the caller must
    separately confirm root itself belongs to the viewer's own family
    (see PersonKvitelView's FamilyScopedMixin), since this function has
    no way to tell "the one visible in-law" from their own unrelated
    relatives.

    can_edit=False additionally cuts an invisible person (and their
    whole blood-descendant line) from the walk, same tradeoff
    visible_people_for_tree already makes for the family tree UI - see
    that function's own docstring. root itself is never cut.
    """
    family = root.family
    rows = Person.objects.filter(family=family).values_list(
        "id", "first_name_he", "gender", "father_id", "mother_id", "is_living", "dob_gregorian"
    )
    people: dict[int, _PersonRow] = {}
    children_of: dict[int, list[int]] = defaultdict(list)
    for person_id, first_name_he, gender, father_id, mother_id, is_living, dob_gregorian in rows:
        people[person_id] = _PersonRow(first_name_he, gender, mother_id, is_living, dob_gregorian)
        if father_id is not None:
            children_of[father_id].append(person_id)
        if mother_id is not None:
            children_of[mother_id].append(person_id)

    if not can_edit:
        visibility = PersonVisibility(viewer)
        cut_ids: set[int] = set()
        for person in Person.objects.filter(family=family):
            if person.id != root.id and not visibility.can_see(person, can_edit=False):
                cut_ids.add(person.id)
                cut_ids.update(_descendant_ids(person.id, children_of))
        cut_ids.discard(root.id)
        if cut_ids:
            people = {pid: row for pid, row in people.items() if pid not in cut_ids}
            children_of = {
                parent_id: [child_id for child_id in child_ids if child_id not in cut_ids]
                for parent_id, child_ids in children_of.items()
                if parent_id not in cut_ids
            }

    # Both sides, not either - a marriage to an in-law tracked only in a
    # different family is out of scope for this family's own kvitel
    # (same "doesn't walk into a cross-tenant in-law" rule as root's own
    # scoping), and treating one as an unresolvable spouse_of id would
    # crash the render() below the moment it tried to look that id up in
    # `people`, which was never fetched for a different family at all.
    person_ids = set(people)
    unions = Union.objects.filter(
        person_a_id__in=person_ids, person_b_id__in=person_ids, status=Union.Status.MARRIED
    ).values_list("person_a_id", "person_b_id")
    spouse_of: dict[int, int] = {}
    for person_a_id, person_b_id in unions:
        spouse_of.setdefault(person_a_id, person_b_id)
        spouse_of.setdefault(person_b_id, person_a_id)

    def label(person_id: int) -> str | None:
        row = people[person_id]
        mother = people.get(row.mother_id) if row.mother_id is not None else None
        return _matronymic_label(row.first_name_he, row.gender, mother.first_name_he if mother else None)

    def living_children(member_a_id: int | None, member_b_id: int | None) -> list[int]:
        """This couple's own living children, oldest to youngest (nulls - no recorded DOB - last)."""
        candidates: set[int] = set()
        if member_a_id is not None:
            candidates.update(children_of.get(member_a_id, []))
        if member_b_id is not None:
            candidates.update(children_of.get(member_b_id, []))
        living = [pid for pid in candidates if people[pid].is_living]
        return sorted(living, key=lambda pid: (people[pid].dob_gregorian is None, people[pid].dob_gregorian))

    def is_complex(person_id: int) -> bool:
        """Has a living spouse, or any living children of their own - warrants a nested block, not a bare line."""
        spouse_id = spouse_of.get(person_id)
        if spouse_id is not None and people[spouse_id].is_living:
            return True
        return bool(living_children(person_id, spouse_id))

    def render(person_id: int, seen: set[int]) -> list[KvitelLine]:
        if person_id in seen:
            return []
        spouse_id = spouse_of.get(person_id)
        if spouse_id is not None and spouse_id in seen:
            # This couple's own block was already rendered starting from
            # the spouse - both members can independently show up as a
            # "complex child" of their own respective parents (e.g. two
            # blood relatives from different branches who married each
            # other), which would otherwise render the same couple twice.
            seen.add(person_id)
            return []
        seen.add(person_id)

        # Swap whenever person_id isn't confidently the husband and
        # there's a reason to think spouse_id is - either spouse_id is
        # explicitly male, or person_id is explicitly female. Checking
        # both sides (not just "is spouse_id male") matters because
        # gender isn't always recorded - found for real against live
        # data where a wife was correctly marked female but her husband
        # had no gender on file at all, and requiring both explicit
        # genders left her listed unprefixed as "husband" instead.
        person_is_male = people[person_id].gender == Person.Gender.MALE
        spouse_is_male = spouse_id is not None and people[spouse_id].gender == Person.Gender.MALE
        should_swap = (
            spouse_id is not None
            and not person_is_male
            and (spouse_is_male or people[person_id].gender == Person.Gender.FEMALE)
        )
        if should_swap:
            husband_id, wife_id = spouse_id, person_id
        else:
            husband_id, wife_id = person_id, spouse_id

        lines: list[KvitelLine] = []
        if people[husband_id].is_living:
            husband_label = label(husband_id)
            if husband_label:
                lines.append(KvitelLine(husband_label, "husband"))
        if wife_id is not None and people[wife_id].is_living:
            wife_label = label(wife_id)
            if wife_label:
                lines.append(KvitelLine(f"ז' {wife_label}", "wife"))

        children = living_children(husband_id, wife_id)
        simple_children = [child_id for child_id in children if not is_complex(child_id)]
        complex_children = [child_id for child_id in children if is_complex(child_id)]

        for child_id in simple_children:
            child_label = people[child_id].first_name_he
            if child_label:
                lines.append(KvitelLine(f"ב' {child_label}", "child"))

        if lines:
            lines[-1] = dataclasses.replace(lines[-1], ends_block=True)

        for child_id in complex_children:
            lines.extend(render(child_id, seen))

        return lines

    return render(root.id, set())
