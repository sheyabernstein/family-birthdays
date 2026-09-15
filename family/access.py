"""Cross-tenant and field-level visibility rules.

A Person belongs to exactly one Family. But marriages cross family lines -
an in-law's own record lives in their own family's ledger. So visibility
isn't just "same family": a person is also visible if they're married to
someone in the viewer's family. This is the one deliberate crack in the
tenant boundary, and it's why these checks exist as their own module
instead of being inlined as a queryset filter everywhere.

Also home to field-level rules that aren't "can you see this record at
all" but "can you see this one part of it" - see can_see_birth_year.
"""

from django.db.models import Q, QuerySet

from family.models import Person, Union
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
