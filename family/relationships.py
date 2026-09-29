"""Blood/marriage relationship-distance checks between two people in the same family.

Pure Person-to-Person primitives - no Account, no notion of "viewer"
beyond "one Person compared to another". notifications.audience wraps
these with an Account -> Person indirection (an account's own linked
Person stands in as the viewer) to resolve notification eligibility;
Person.visibility (see AGENTS.md) wraps them again, the same way, to
gate what a plain member sees in the People list and family tree. Both
consumers share this one definition of "immediate family"/"direct
family" rather than each re-deriving it independently.
"""

from collections.abc import Callable

from django.db import models

from family.models import Person, Union, bfs_relative_ids


def _is_spouse(a: Person, b: Person) -> bool:
    return Union.objects.filter(
        (models.Q(person_a=a, person_b=b) | models.Q(person_a=b, person_b=a)),
        status=Union.Status.MARRIED,
    ).exists()


def is_immediate_family(
    viewer: Person, subject: Person, *, spouse_check: Callable[[Person, Person], bool] = _is_spouse
) -> bool:
    """Spouse, parent, child, or sibling - the fixed, non-configurable definition of "immediate family".

    Used for the immediate_family_only preference. Everyone else
    (grandparents, cousins, in-laws beyond a spouse, ...) is "wider
    family" and can only be reached via an explicit per-person override.

    `spouse_check` defaults to a live DB lookup (_is_spouse), but callers
    resolving this for many (viewer, subject) pairs at once - see
    notifications.audience.PreferenceResolver - pass in a cache-backed
    version instead, so this stays a single, shared definition of
    "immediate family" either way.
    """
    if viewer.id == subject.id:
        return True
    if subject.father_id == viewer.id or subject.mother_id == viewer.id:
        return True  # subject is viewer's child
    if viewer.father_id == subject.id or viewer.mother_id == subject.id:
        return True  # subject is viewer's parent
    if viewer.father_id is not None and viewer.father_id in (subject.father_id, subject.mother_id):
        return True  # shares a father with subject - sibling (incl. half-sibling)
    if viewer.mother_id is not None and viewer.mother_id in (subject.father_id, subject.mother_id):
        return True  # shares a mother with subject - sibling (incl. half-sibling)
    return spouse_check(viewer, subject)


def _ancestor_ids(person_id: int) -> set[int]:
    """Every id in person_id's own direct father/mother line, via BFS over father_id/mother_id.

    Built on the same bfs_relative_ids Person.descendant_ids() uses, just
    climbing instead of descending - only the per-generation query
    differs. "Ancestor" here is direct line only: a grandparent's own
    sibling isn't included, just the grandparent (and their own
    parents, ...) themselves.
    """

    def _parents(frontier: set[int]) -> set[int]:
        parent_ids = Person.objects.filter(pk__in=frontier).values_list("father_id", "mother_id")
        return {pid for pair in parent_ids for pid in pair if pid is not None}

    return bfs_relative_ids({person_id}, _parents)


def _ancestor_ids_for(viewer: Person) -> set[int]:
    return _ancestor_ids(viewer.id)


def is_ancestor(
    viewer: Person, subject: Person, *, ancestor_ids_fn: Callable[[Person], set[int]] = _ancestor_ids_for
) -> bool:
    """Whether subject is somewhere in viewer's own direct father/mother line.

    One of the three ingredients of is_direct_family (below) - not
    used as a standalone whole-type preference on its own. `ancestor_ids_fn`
    defaults to a live BFS-by-recursive-query (_ancestor_ids_for), but
    callers resolving this for many (viewer, subject) pairs at once -
    see notifications.audience.PreferenceResolver - pass in a cache-backed
    version instead, so the same viewer's ancestor chain is only ever
    computed once regardless of how many subjects it's checked against.
    """
    if viewer.id == subject.id:
        return False
    return subject.id in ancestor_ids_fn(viewer)


def _descendant_ids_for(viewer: Person) -> set[int]:
    return viewer.descendant_ids()


def is_descendant(
    viewer: Person, subject: Person, *, descendant_ids_fn: Callable[[Person], set[int]] = _descendant_ids_for
) -> bool:
    """Whether subject is somewhere in viewer's own descendant tree, at any depth.

    The "down" counterpart to is_ancestor - see that function's own
    docstring and is_direct_family below for how the two combine.
    """
    if viewer.id == subject.id:
        return False
    return subject.id in descendant_ids_fn(viewer)


def memoize_by_person(compute: Callable[[Person], set[int]]) -> Callable[[Person], set[int]]:
    """Wraps a Person -> set[int] lookup (e.g. _ancestor_ids_for/_descendant_ids_for) with a per-person cache.

    family.access.PersonVisibility and notifications.audience.
    PreferenceResolver each need this same get-or-compute shape, so a
    viewer's own ancestor/descendant chain is only ever computed once
    regardless of how many candidates it's checked against - see
    PersonVisibility's own docstring. Build a fresh one per
    request/task; it isn't meant to be shared across callers.
    """
    cache: dict[int, set[int]] = {}

    def cached(person: Person) -> set[int]:
        ids = cache.get(person.id)
        if ids is None:
            ids = compute(person)
            cache[person.id] = ids
        return ids

    return cached


def _spouses_of(person: Person) -> list[Person]:
    """Every person currently married to `person` - almost always zero or one, never assumed to be at most one."""
    unions = Union.objects.filter(
        (models.Q(person_a=person) | models.Q(person_b=person)), status=Union.Status.MARRIED
    ).select_related("person_a", "person_b")
    return [union.other(person) for union in unions]


def is_direct_family(
    viewer: Person,
    subject: Person,
    *,
    spouse_check: Callable[[Person, Person], bool] = _is_spouse,
    ancestor_ids_fn: Callable[[Person], set[int]] = _ancestor_ids_for,
    descendant_ids_fn: Callable[[Person], set[int]] = _descendant_ids_for,
    spouses_of_fn: Callable[[Person], list[Person]] = _spouses_of,
) -> bool:
    """Immediate family, the whole ancestor/descendant line, and the same again through one marriage hop.

    Used for the direct_family_only preference (Yahrzeit's own
    default). Three ingredients, unioned:

    - is_immediate_family(viewer, subject) - spouse/parent/child/sibling,
      the fixed one-generation definition.
    - is_ancestor(viewer, subject) - viewer's own ancestor line, any depth
      (a great-grandparent isn't "immediate family" by the definition
      above, but should still reach every descendant, however distant).
    - is_descendant(viewer, subject) - the mirror image: viewer's own
      descendant line, any depth (a still-living great-grandparent should
      hear about a great-grandchild's yahrzeit the same way the reverse
      case works).

    All three are also checked against viewer's own current spouse(s), not
    just viewer directly - a spouse's own family reaches you too (their
    grandparent is your in-law's yahrzeit to hear about), the same way
    marrying in makes someone "immediate family" in the first place. This
    is deliberately bounded to one marriage hop from viewer - it does not
    also reach through, say, a child's own spouse's family, which would
    turn this into an unbounded walk across blood *and* marriage edges
    together and stop being a meaningfully narrower circle than
    "everyone".
    """

    def _covers(person: Person) -> bool:
        return (
            is_immediate_family(person, subject, spouse_check=spouse_check)
            or is_ancestor(person, subject, ancestor_ids_fn=ancestor_ids_fn)
            or is_descendant(person, subject, descendant_ids_fn=descendant_ids_fn)
        )

    if _covers(viewer):
        return True
    return any(_covers(spouse) for spouse in spouses_of_fn(viewer))


def person_visible_to(
    viewer: Person | None,
    subject: Person,
    *,
    spouse_check: Callable[[Person, Person], bool] = _is_spouse,
    ancestor_ids_fn: Callable[[Person], set[int]] = _ancestor_ids_for,
    descendant_ids_fn: Callable[[Person], set[int]] = _descendant_ids_for,
    spouses_of_fn: Callable[[Person], list[Person]] = _spouses_of,
) -> bool:
    """Whether `viewer` may see `subject` at all, per subject.visibility.

    Shared by family.access.PersonVisibility (People list/tree) and
    notifications.audience.PreferenceResolver (notification eligibility)
    - one definition of what each Person.visibility level means,
    regardless of which of those wraps it. Neither "owners/editors
    always see everyone" nor any caching lives here - each caller
    applies its own bypass/cache first, since "can edit" means something
    different in each context (a family role vs. nothing this module
    knows about).
    """
    if subject.visibility == Person.Visibility.EVERYONE:
        return True
    if viewer is None:
        return False
    if subject.visibility == Person.Visibility.NOBODY:
        return False
    if subject.visibility == Person.Visibility.IMMEDIATE_FAMILY:
        return is_immediate_family(viewer, subject, spouse_check=spouse_check)
    if subject.visibility == Person.Visibility.DIRECT_FAMILY:
        return is_direct_family(
            viewer,
            subject,
            spouse_check=spouse_check,
            ancestor_ids_fn=ancestor_ids_fn,
            descendant_ids_fn=descendant_ids_fn,
            spouses_of_fn=spouses_of_fn,
        )
    raise ValueError(f"Unrecognized Person.Visibility: {subject.visibility!r}")
