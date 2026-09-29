"""Cross-tenant, field-level, and within-family visibility rules.

A Person belongs to exactly one Family. But marriages cross family lines -
an in-law's own record lives in their own family's ledger. So visibility
isn't just "same family": a person is also visible if they're married to
someone in the viewer's family. This is the one deliberate crack in the
tenant boundary, and it's why these checks exist as their own module
instead of being inlined as a queryset filter everywhere.

Also home to field-level rules that aren't "can you see this record at
all" but "can you see this one part of it" - see can_see_birth_year - and
to Person.visibility itself (see PersonVisibility below), a *within*-
family ceiling on top of the cross-tenant rules above: a person can be
visible to a family per person_is_visible and still invisible to a
particular plain member of it, per their own relationship distance.
"""

from collections.abc import Iterable

from django.db.models import Q, QuerySet

from family.models import Person, Union
from family.relationships import _ancestor_ids, _spouses_of, person_visible_to
from tenants.models import Family


def person_is_visible(person: Person, family: Family | None) -> bool:
    """Reports whether a person is visible to a given family.

    True when the person belongs to that family directly, or is married
    to someone who does (the one deliberate cross-tenant exception - see
    this module's own docstring).
    """
    if family is None:
        return False
    if person.family_id == family.id:
        return True
    return Union.objects.filter(
        Q(person_a=person, person_b__family=family) | Q(person_b=person, person_a__family=family)
    ).exists()


def union_is_visible(union: Union, family: Family | None) -> bool:
    """Reports whether a marriage is visible to a given family.

    True when either spouse belongs to that family - the same
    cross-tenant exception as person_is_visible, from the marriage's own
    side.
    """
    if family is None:
        return False
    return union.person_a.family_id == family.id or union.person_b.family_id == family.id


def can_see_birth_year(person: Person, *, can_edit: bool, viewer_account_id: int | None) -> bool:
    """Whether the current viewer may see this person's birth year.

    Editors/owners always can (see tenants.permissions.FamilyPermissions)
    - a plain member can't see anyone's birth year *except their own*,
    since that's their own age to share or not, not someone else's
    private information. Month/day still show for everyone regardless -
    only the year is restricted (see family.hebrew.format_hebrew_date's
    include_year and Person.dob_hebrew_month_day_display).
    """
    if can_edit:
        return True
    return viewer_account_id is not None and person.account_id == viewer_account_id


def visible_people_queryset(family: Family | None) -> QuerySet[Person]:
    """Bulk form of person_is_visible.

    Everyone in this family's own ledger, plus any in-law reachable
    through a marriage to one of them.
    """
    if family is None:
        return Person.objects.none()
    return Person.objects.filter(
        Q(family=family) | Q(unions_as_a__person_b__family=family) | Q(unions_as_b__person_a__family=family)
    ).distinct()


class PersonVisibility:
    """Caches one fixed viewer's own relationship reach, for checking many candidate people cheaply.

    is_immediate_family/is_direct_family (family.relationships) each
    recompute the viewer's own ancestor/descendant chain from scratch by
    default - fine for a single (viewer, person) pair, a real N+1 across
    a whole People list or family tree (see AGENTS.md's own notes on
    this - same shape as notifications.audience.PreferenceResolver, just
    scoped to one viewer instead of every account in a family). Construct
    once per request/page, then call can_see() for every candidate.
    """

    def __init__(self, viewer: Person | None) -> None:
        self._viewer = viewer
        self._ancestor_ids_cache: dict[int, set[int]] = {}
        self._descendant_ids_cache: dict[int, set[int]] = {}
        self._spouses_cache: dict[int, list[Person]] = {}

    def _ancestor_ids(self, person: Person) -> set[int]:
        ids = self._ancestor_ids_cache.get(person.id)
        if ids is None:
            ids = _ancestor_ids(person.id)
            self._ancestor_ids_cache[person.id] = ids
        return ids

    def _descendant_ids(self, person: Person) -> set[int]:
        ids = self._descendant_ids_cache.get(person.id)
        if ids is None:
            ids = person.descendant_ids()
            self._descendant_ids_cache[person.id] = ids
        return ids

    def _spouses_of(self, person: Person) -> list[Person]:
        spouses = self._spouses_cache.get(person.id)
        if spouses is None:
            spouses = _spouses_of(person)
            self._spouses_cache[person.id] = spouses
        return spouses

    def can_see(self, person: Person, *, can_edit: bool) -> bool:
        """Whether this checker's own viewer may see `person` at all, per Person.visibility.

        Owners/editors always see everyone (can_edit=True short-circuits
        this, same shape as can_see_birth_year above) - the rest defers
        to person_visible_to for what each visibility level actually means.
        """
        if can_edit:
            return True
        return person_visible_to(
            self._viewer,
            person,
            ancestor_ids_fn=self._ancestor_ids,
            descendant_ids_fn=self._descendant_ids,
            spouses_of_fn=self._spouses_of,
        )


class PersonAccessContext:
    """Single source for Person visibility/access decisions with internal caching.

    Consolidates visibility, role-based access, and relationship distance checks
    into one cohesive API. Use one per request/task for checking multiple people
    (access is cached), or the single-pair person_is_visible_to() convenience
    function for just one.

    Args:
        viewer: The person checking access (None for anon/unknown viewers).
        is_editor: Whether the viewer has edit permissions (owner/editor role).
    """

    def __init__(self, viewer: Person | None, is_editor: bool = False) -> None:
        self.viewer = viewer
        self.is_editor = is_editor
        self._ancestor_ids_cache: dict[int, set[int]] = {}
        self._descendant_ids_cache: dict[int, set[int]] = {}
        self._spouses_cache: dict[int, list[Person]] = {}

    def _get_ancestor_ids(self, person: Person) -> set[int]:
        ids = self._ancestor_ids_cache.get(person.id)
        if ids is None:
            ids = _ancestor_ids(person.id)
            self._ancestor_ids_cache[person.id] = ids
        return ids

    def _get_descendant_ids(self, person: Person) -> set[int]:
        ids = self._descendant_ids_cache.get(person.id)
        if ids is None:
            ids = person.descendant_ids()
            self._descendant_ids_cache[person.id] = ids
        return ids

    def _get_spouses_of(self, person: Person) -> list[Person]:
        spouses = self._spouses_cache.get(person.id)
        if spouses is None:
            spouses = _spouses_of(person)
            self._spouses_cache[person.id] = spouses
        return spouses

    def _cached_spouse_check(self, a: Person, b: Person) -> bool:
        return b in self._get_spouses_of(a) or a in self._get_spouses_of(b)

    def can_see(self, subject: Person) -> bool:
        """Whether the viewer can see this person at all.

        Returns True if:
        - The viewer is an editor/owner (can_edit=True), OR
        - The person's visibility allows it (checks Person.visibility field
          and relationship distance if needed)
        """
        if self.is_editor:
            return True
        return person_visible_to(
            self.viewer,
            subject,
            spouse_check=self._cached_spouse_check,
            ancestor_ids_fn=self._get_ancestor_ids,
            descendant_ids_fn=self._get_descendant_ids,
            spouses_of_fn=self._get_spouses_of,
        )


def person_is_visible_to(person: Person, *, viewer: Person | None, can_edit: bool) -> bool:
    """Single-pair convenience over PersonVisibility, for checking just one person.

    Checking many people for the same viewer (People list, family tree)
    should build one PersonVisibility and call can_see() repeatedly
    instead, so the viewer's own ancestor/descendant/spouse lookups are
    computed once and shared across every candidate, not recomputed per
    person.
    """
    return PersonVisibility(viewer).can_see(person, can_edit=can_edit)


def visible_people_for_tree(
    people: Iterable[Person], *, viewer: Person | None, can_edit: bool, keep_id: int
) -> list[Person]:
    """Cuts an invisible person, and their whole descendant line, out of a tree's own node list.

    family-chart has no way to draw a child whose parent isn't in the
    payload at all, so an invisible person's descendants (any depth) are
    cut along with them, not just their own card - see /help/ for the
    tradeoff this accepts (a viewer who could otherwise see a more
    distant descendant loses that too) and why this is addressed with a
    warning at edit time, not a partial-tree workaround.

    keep_id is the tree's own subject (already confirmed visible to get
    this far - see FamilyTreeView.get_object) - it must never be cut just
    because *its own* ancestor happens to be invisible to this viewer;
    Person.visibility restricts a person's own card, not their
    descendants' independent visibility.
    """
    if can_edit:
        return list(people)

    people = list(people)
    visibility = PersonVisibility(viewer)
    cut_ids: set[int] = set()
    for person in people:
        if person.id != keep_id and not visibility.can_see(person, can_edit=False):
            cut_ids.add(person.id)
            cut_ids.update(person.descendant_ids())
    cut_ids.discard(keep_id)
    return [person for person in people if person.id not in cut_ids]
