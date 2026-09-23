"""Serializes people into the data shape family-chart expects.

family-chart is the JS library used by family/tree.html; it expects a flat
list of {id, data: {...}, rels: {parents, spouses, children}}.

See https://donatso.github.io/family-chart/ for the library itself. The
whole visible set is sent in one go rather than paginating by generation -
the library trims what it renders around the focused person (see the
ancestry/progeny depth settings in family_tree.html) and shows a
click-to-expand indicator on any card with relatives outside that window,
so a large family stays a manageable width without the server needing to
know which branch the viewer will explore next.
"""

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from family.models import Person, Union

VALID_GENDERS = {"M", "F"}


def _gender_code(person: Person) -> str:
    # family-chart requires exactly "M" or "F" - people whose gender
    # wasn't recorded (a handful of untracked-ancestor stubs) default to
    # "M" purely for layout purposes; it has no other effect.
    return person.gender if person.gender in VALID_GENDERS else "M"


def _display_name(person: Person) -> tuple[str, str]:
    # first_name_en checked alone, not the "first_name_en or
    # last_name_en" combined truthiness Person.display_name itself used
    # to use before it was fixed - first_name_en/last_name_en are both
    # optional (first_name_he is the one required name), so a person
    # with only a last_name_en on file would otherwise show a bare
    # surname on the card instead of falling back to their Hebrew name.
    # Deliberately still ignores nickname, unlike Person.display_name -
    # a pre-existing inconsistency between the tree and every other
    # display_name call site, not something to fix incidentally here.
    if person.first_name_en:
        return person.first_name_en, person.last_name_en
    return person.hebrew_name or "?", ""


def _birth_rank_by_id(people: list[Person]) -> dict[int, int]:
    """Maps each person with a known birthdate to their birth-order rank.

    An opaque, order-preserving integer (0, 1, 2, ...) rather than
    `dob_gregorian.toordinal()` - family-chart's own sort hook
    (setSortChildrenFunction, see family_tree.html) only ever needs
    relative order among a shared parent's children, never the actual
    date, and an ordinal is trivially reversible back to an exact date
    (`date.fromordinal(n)`) by anyone reading the chart's own JSON
    payload - a real leak regardless of what's rendered as visible text
    (see family.access.can_see_birth_year for why exact birthdates
    aren't universally shown).
    """
    with_dob = sorted((p for p in people if p.dob_gregorian), key=lambda p: p.dob_gregorian)
    return {p.id: rank for rank, p in enumerate(with_dob)}


def build_chart_data(
    people: Iterable[Person], *, main_person: Person, editable_family_id: int | None = None
) -> list[dict[str, Any]]:
    """Builds the family-chart node list for one viewer's tree.

    Args:
        people: Every Person this viewer may see (see
            family.access.visible_people_queryset).
        main_person: Decides which node the chart opens centered on, and
            is put first in the returned list since that's how the
            library picks its default focus.
        editable_family_id: Flags each node with whether it belongs to
            that family (`own_family`) - the tree's "+ add relative"
            placeholders (see family_tree.html) only make sense for
            people in the viewer's own ledger, not an in-law visible only
            through a marriage.

    Returns:
        A list of family-chart node dicts, main_person's node first.
    """
    people = list(people)
    ids = {p.id for p in people}
    birth_rank = _birth_rank_by_id(people)

    children_by_parent = defaultdict(list)
    for p in people:
        if p.father_id in ids:
            children_by_parent[p.father_id].append(str(p.uuid))
        if p.mother_id in ids:
            children_by_parent[p.mother_id].append(str(p.uuid))

    spouses_by_person = defaultdict(list)
    # Which people have a wedding coming up, and to whom - drives the
    # "not married yet" badge on the card (see family_tree.html's
    # setCardInnerHtmlCreator). Union.is_upcoming is computed, not stored
    # (see AGENTS.md), so this is worked out here rather than read off a
    # status field.
    upcoming_wedding_partner: dict[int, str] = {}
    upcoming_wedding_partner_uuid: dict[int, str] = {}
    unions = Union.objects.filter(person_a_id__in=ids, person_b_id__in=ids).select_related(
        "person_a", "person_b"
    )
    for union in unions:
        spouses_by_person[union.person_a_id].append(str(union.person_b.uuid))
        spouses_by_person[union.person_b_id].append(str(union.person_a.uuid))
        if union.is_upcoming:
            upcoming_wedding_partner[union.person_a_id] = union.person_b.display_name
            upcoming_wedding_partner[union.person_b_id] = union.person_a.display_name
            # The uuid pair, not just the badge text, is what lets the JS
            # side single out this exact spouse link (not just "someone
            # in this couple has an upcoming wedding") to dash - see
            # markUpcomingWeddingLinks() in family_tree.html.
            upcoming_wedding_partner_uuid[union.person_a_id] = str(union.person_b.uuid)
            upcoming_wedding_partner_uuid[union.person_b_id] = str(union.person_a.uuid)

    nodes = []
    for p in people:
        first_name, last_name = _display_name(p)
        parents = [str(x.uuid) for x in (p.father, p.mother) if x is not None]
        # Only show the Hebrew name as a second line when it's not already
        # doing double duty as the primary name above (the no-English-name
        # fallback in _display_name).
        name_is_hebrew = not p.first_name_en
        hebrew_name = p.hebrew_name if not name_is_hebrew and p.hebrew_name else ""

        nodes.append(
            {
                "id": str(p.uuid),
                "data": {
                    "first name": first_name,
                    "last name": last_name,
                    "gender": _gender_code(p),
                    "hebrew_name": hebrew_name,
                    # Drives the card's own RTL wrapping for the deceased
                    # marker (see family_tree.html's setCardInnerHtmlCreator
                    # and family_extras.display_name_with_marker's own
                    # docstring for why plain concatenation with an
                    # isolated-RTL marker span breaks once the *name*
                    # itself is Hebrew) - derived from the exact same
                    # condition _display_name() used above, not
                    # Person.display_name_is_hebrew, so this always
                    # agrees with what "first name"/"last name" actually
                    # resolved to on this node (display_name_is_hebrew
                    # also considers a Hebrew nickname, which the tree's
                    # own _display_name() ignores entirely - a
                    # pre-existing inconsistency, not something to paper
                    # over here).
                    "name_is_hebrew": name_is_hebrew,
                    "birth_sort": birth_rank.get(p.id),
                    "living": p.is_living,
                    "own_family": p.family_id == editable_family_id,
                    "upcoming_wedding_partner": upcoming_wedding_partner.get(p.id, ""),
                    "upcoming_wedding_partner_id": upcoming_wedding_partner_uuid.get(p.id, ""),
                },
                "rels": {
                    "parents": parents,
                    "spouses": spouses_by_person.get(p.id, []),
                    "children": children_by_parent.get(p.id, []),
                },
            }
        )

    main_uuid = str(main_person.uuid)
    nodes.sort(key=lambda n: n["id"] != main_uuid)
    return nodes
