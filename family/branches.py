"""Per-export "which branch does this person belong to" coloring.

Can't be inferred from the data alone: "no recorded father/mother" is
usually just an in-law whose own parents were never entered, not a real
family founder, and the full father/mother/spouse graph collapses into
one connected component once enough cross-cousin marriages exist (both
confirmed against the real Rokach data, not assumed). So Family.tree_root
is a human-designated anchor instead - see compute_branches for exactly
what branches that from.
"""

from collections import defaultdict

from django.db import models

from family.models import Person, Union
from tenants.models import Family

# Cycles if a family has more branches than this - fine, a repeated
# color on a family with more than twenty branches just stops
# disambiguating every one of them, same degradation as running out of
# distinct colors in the original spreadsheet this mirrors.
BRANCH_COLORS: list[str] = [
    "FCE5CD",
    "FFF2CC",
    "D9EAD3",
    "D0E0E3",
    "CFE2F3",
    "D9D2E9",
    "EAD1DC",
    "F4CCCC",
    "FFE5A0",
    "C9DAF8",
    "B6D7A8",
    "A2C4C9",
    "A4C2F4",
    "B4A7D6",
    "D5A6BD",
    "EA9999",
    "F9CB9C",
    "FFD966",
    "D9B38C",
    "CCCCFF",
]


def branch_color(index: int) -> str:
    """The 6-hex-digit (no '#') fill color for a given branch index."""
    return BRANCH_COLORS[index % len(BRANCH_COLORS)]


def compute_branches(family: Family) -> dict[int, int]:
    """Maps a Person id to its 0-based branch index, omitting anyone with none.

    One branch per child of family.tree_root, ordered by pk (creation
    order), not name - so a color is stable export to export: adding a
    new child later only ever appends a branch, it never reshuffles an
    existing one's index (and so its color) the way sorting by name
    would whenever the new child's name happens to sort earlier than an
    existing one's. Blood wins on conflict - a person already assigned
    via an earlier child's descendant walk keeps that assignment, the
    deterministic tiebreak for a cross-branch marriage's own child.
    Anyone with no blood path to tree_root inherits their spouse's
    branch instead, one hop only - not a transitive chain of further
    in-laws' own in-laws.
    """
    if family.tree_root_id is None:
        return {}

    # One row per family member, ordered by pk (creation order - see
    # above) so children_of's own lists come out in that order too.
    rows = Person.objects.filter(family=family).order_by("pk").values_list("id", "father_id", "mother_id")
    children_of: dict[int, list[int]] = defaultdict(list)
    person_ids: set[int] = set()
    for person_id, father_id, mother_id in rows:
        person_ids.add(person_id)
        if father_id is not None:
            children_of[father_id].append(person_id)
        if mother_id is not None:
            children_of[mother_id].append(person_id)

    assigned: dict[int, int] = {}
    for index, child_id in enumerate(children_of.get(family.tree_root_id, [])):
        # seen, not just assigned.setdefault, bounds this walk to each
        # id once - a descendant reachable through two paths within the
        # same branch (e.g. a cousin marriage) would otherwise get
        # re-expanded from scratch every time it's reached again.
        seen: set[int] = set()
        frontier = [child_id]
        while frontier:
            next_frontier = []
            for person_id in frontier:
                if person_id in seen:
                    continue
                seen.add(person_id)
                assigned.setdefault(person_id, index)
                next_frontier.extend(children_of.get(person_id, []))
            frontier = next_frontier

    unions = Union.objects.filter(
        (models.Q(person_a_id__in=person_ids) | models.Q(person_b_id__in=person_ids)),
        status=Union.Status.MARRIED,
    ).values_list("person_a_id", "person_b_id")
    spouses_of: dict[int, list[int]] = defaultdict(list)
    for person_a_id, person_b_id in unions:
        spouses_of[person_a_id].append(person_b_id)
        spouses_of[person_b_id].append(person_a_id)

    # Snapshot before this loop and check membership against it, not the
    # live `assigned` dict - otherwise an in-law colored earlier in this
    # same pass could itself get read as a source by a later one,
    # chaining two marriage-hops deep instead of the one this is meant
    # to stop at.
    blood_only = dict(assigned)
    for person_id in person_ids:
        if person_id in blood_only:
            continue
        for spouse_id in spouses_of.get(person_id, []):
            if spouse_id in blood_only:
                assigned[person_id] = blood_only[spouse_id]
                break

    return assigned
