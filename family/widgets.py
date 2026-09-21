"""The father/mother/existing_spouse picker widget and its supporting helpers.

Split out of forms.py since this is a self-contained rendering concern
(how a Person shows up as a dropdown option), not form validation or
provisioning logic. See AGENTS.md's father/mother/spouse picker bullets
for why any of this exists.
"""

from collections.abc import Callable
from typing import Any

from django import forms
from django.db.models import Prefetch, QuerySet

from family.models import Person, Union


def _year_label(person: Person) -> str:
    """Formats a person's birth year as "b. 1994", "b. 1994 AM", or "birth year unknown".

    Shared by the plain-text option label (the no-JS fallback and Tom
    Select's own text search) and PersonPickerSelect's data-birth-year
    attribute (the JS-rendered multi-line option - see
    person_form.html/union_form.html).
    """
    if person.dob_gregorian:
        return f"b. {person.dob_gregorian.year}"
    if person.dob_hebrew_year:
        return f"b. {person.dob_hebrew_year} AM"
    return "birth year unknown"


def _gender_warning(person: Person, expected_gender: str) -> str:
    """Flags a parent whose own gender doesn't match the field they're stored in.

    E.g. a father who's actually recorded female - existing bad data from
    before this field was gender-filtered, not something the form itself
    can produce going forward. Surfacing it here beats letting it sit
    invisibly wrong.
    """
    if person.gender and person.gender != expected_gender:
        return "wrong gender on file"
    return ""


def prefetch_for_person_picker(queryset: QuerySet[Person]) -> QuerySet[Person]:
    """Prefetches everything _relations_hint needs, so rendering a picker's options never scales into an N+1.

    Shared by PersonForm's father/mother fields and UnionForm's
    existing_spouse field - all three render through the same
    PersonPickerSelect + _person_option_label machinery, and a picker
    can show a couple hundred people (a normal ledger size here) - one
    query per relation kind here, not one per candidate per kind.
    """
    married_unions = Union.objects.filter(status=Union.Status.MARRIED)
    return queryset.select_related("father", "mother").prefetch_related(
        "children_as_father",
        "children_as_mother",
        Prefetch("unions_as_a", queryset=married_unions.select_related("person_b")),
        Prefetch("unions_as_b", queryset=married_unions.select_related("person_a")),
    )


def _parents_hint(person: Person) -> str:
    """A short "child of X & Y" fragment, naming every recorded parent regardless of living/tracked status.

    Unlike Person.parents_label (warm notification copy, living/tracked
    parents only - see that property's own docstring), the point here is
    telling two same-named ledger entries apart while picking one, so an
    untracked or deceased parent's name is exactly the clue that
    matters - maybe more so, since older imported records often have
    both parents already deceased.
    """
    names = [p.display_name for p in (person.father, person.mother) if p is not None]
    return "child of " + " & ".join(names) if names else ""


def _spouse_hint(person: Person) -> str:
    """A short "spouse of X" fragment - reads person.unions_as_a/_b, expected to already be prefetched."""
    names = [union.person_b.display_name for union in person.unions_as_a.all()]
    names += [union.person_a.display_name for union in person.unions_as_b.all()]
    return "spouse of " + " & ".join(names) if names else ""


def _children_hint(person: Person) -> str:
    """A short "parent of X, Y" fragment - reads the two children_as_* relations, expected to already be prefetched."""
    names = [child.display_name for child in person.children_as_father.all()]
    names += [child.display_name for child in person.children_as_mother.all()]
    return "parent of " + ", ".join(names) if names else ""


def _relations_hint(person: Person) -> str:
    """Combines the parent/spouse/child hints above into one line - whichever of them apply.

    Found for real: a two-person cycle created by picking the wrong
    same-named, no-distinguishing-info person from this exact picker
    (see AGENTS.md) - a record with no recorded parents (common for an
    older imported record) still has a real spouse or children to name
    instead, often a more useful clue than parentage for exactly the
    kind of sparse, easily-confused record that caused that incident.
    """
    fragments = [
        hint for hint in (_parents_hint(person), _spouse_hint(person), _children_hint(person)) if hint
    ]
    return "; ".join(fragments)


def _person_option_label(person: Person) -> str:
    """Builds option text for a father/mother/spouse picker.

    A bare name is useless for disambiguating a large ledger's many
    repeated names (two "Blimi Rokach"s is normal in a family this
    size), so every option also shows a birth year, or says there isn't
    one, plus a relations hint (parents/spouse/children - see
    _relations_hint) when any of those are on file.
    """
    label = f"{person.display_name} ({_year_label(person)})"
    relations_hint = _relations_hint(person)
    return f"{label} - {relations_hint}" if relations_hint else label


def _parent_option_label(expected_gender: str) -> Callable[[Person], str]:
    def label(person: Person) -> str:
        base = _person_option_label(person)
        warning = _gender_warning(person, expected_gender)
        return f"{base} - {warning}" if warning else base

    return label


class _PersonOptionMixin:
    """Shared create_option() decoration for the single and multi person pickers.

    Used by both the single father/mother/spouse picker
    (PersonPickerSelect) and the multi-select broadcast people picker
    (PersonMultiPickerSelect) - the multi-line Tom Select rendering
    (person_picker.js) reads the same data-* attributes regardless of
    whether the underlying <select> takes one value or many.
    """

    expected_gender: str | None = None

    def create_option(
        self,
        name: str,
        value: Any,
        label: str,
        selected: bool,
        index: int,
        subindex: int | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        person = getattr(value, "instance", None)
        if isinstance(person, Person):
            option["attrs"]["data-display-name"] = person.display_name
            option["attrs"]["data-birth-year"] = _year_label(person)
            if person.first_name_he and person.display_name != person.first_name_he:
                option["attrs"]["data-hebrew-first-name"] = person.first_name_he
            relations_hint = _relations_hint(person)
            if relations_hint:
                option["attrs"]["data-relations-hint"] = relations_hint
            if self.expected_gender:
                warning = _gender_warning(person, self.expected_gender)
                if warning:
                    option["attrs"]["data-warning"] = warning
        return option


class PersonPickerSelect(_PersonOptionMixin, forms.Select):
    """Adds data-* attributes to each <option> for the multi-line Tom Select rendering.

    Adds data-display-name/data-hebrew-first-name/data-birth-year/
    data-relations-hint/data-warning attributes to each <option>, on top
    of the plain-text label every ModelChoiceField already gets. Tom
    Select doesn't read arbitrary data-* attributes on its own (confirmed
    against the actual library - it only picks up value/text/disabled),
    so person_form.html/union_form.html's JS re-reads these via each
    option's .dataset after init and merges them in with
    ts.updateOption(), then renders a multi-line option (name + year,
    Hebrew first name subtitle, a "child of ..."/"spouse of ..."/
    "parent of ..." relations hint) from them instead of Tom Select's
    default plain text. The <option> text itself is untouched - it's
    still the single combined string, used as the no-JS fallback and as
    one of Tom Select's search fields.

    expected_gender is optional: only the father/mother fields need the
    wrong-gender warning; existing_spouse has no "expected" gender to
    compare against (it's already restricted to the opposite of
    person_a's, when known - see UnionForm).
    """

    def __init__(self, *args, expected_gender: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.expected_gender = expected_gender


class PersonMultiPickerSelect(_PersonOptionMixin, forms.SelectMultiple):
    """Same multi-line Tom Select rendering as PersonPickerSelect, for a multi-select.

    Used for a ModelMultipleChoiceField (the broadcast people picker) -
    no expected_gender, since a broadcast isn't filtered by relation role.
    """
