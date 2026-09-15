"""The father/mother/existing_spouse picker widget and its supporting helpers.

Split out of forms.py since this is a self-contained rendering concern
(how a Person shows up as a dropdown option), not form validation or
provisioning logic. See AGENTS.md's father/mother/spouse picker bullets
for why any of this exists.
"""

from collections.abc import Callable
from typing import Any

from django import forms

from family.models import Person


def _year_label(person: Person) -> str:
    """Formats a person's birth year as "b. 1994", "b. 1994 AM", or "birth year unknown".

    Shared by the plain-text option label (the no-JS fallback and Tom
    Select's own text search) and PersonPickerSelect's data-birth-year
    attribute (the JS-rendered two-line option - see
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


def _person_option_label(person: Person) -> str:
    """Builds option text for a father/mother/spouse picker.

    A bare name is useless for disambiguating a large ledger's many
    repeated names (two "Blimi Rokach"s is normal in a family this
    size), so every option also shows a birth year, or says there isn't
    one.
    """
    return f"{person.display_name} ({_year_label(person)})"


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
    (PersonMultiPickerSelect) - the two-line Tom Select rendering
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
            if self.expected_gender:
                warning = _gender_warning(person, self.expected_gender)
                if warning:
                    option["attrs"]["data-warning"] = warning
        return option


class PersonPickerSelect(_PersonOptionMixin, forms.Select):
    """Adds data-* attributes to each <option> for the two-line Tom Select rendering.

    Adds data-display-name/data-hebrew-first-name/data-birth-year/
    data-warning attributes to each <option>, on top of the plain-text
    label every ModelChoiceField already gets. Tom Select doesn't read
    arbitrary data-* attributes on its own (confirmed against the actual
    library - it only picks up value/text/disabled), so
    person_form.html/union_form.html's JS re-reads these via each
    option's .dataset after init and merges them in with
    ts.updateOption(), then renders a two-line option (name + year,
    Hebrew first name subtitle) from them instead of Tom Select's
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
    """Same two-line Tom Select rendering as PersonPickerSelect, for a multi-select.

    Used for a ModelMultipleChoiceField (the broadcast people picker) -
    no expected_gender, since a broadcast isn't filtered by relation role.
    """
