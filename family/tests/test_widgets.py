import datetime as dt

import pytest

from family.forms import PersonForm, UnionForm
from family.models import Person
from family.widgets import PersonPickerSelect, _gender_warning, _year_label

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ["dob_gregorian", "dob_hebrew_year", "expected"],
    [
        [dt.date(1994, 3, 1), None, "b. 1994"],
        [None, 5754, "b. 5754 AM"],
        [None, None, "birth year unknown"],
    ],
    ids=[
        "gregorian dob takes priority",
        "falls back to hebrew year when no gregorian dob",
        "no dob recorded at all",
    ],
)
def test_year_label(family, dob_gregorian, dob_hebrew_year, expected):
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_gregorian=dob_gregorian,
        dob_hebrew_year=dob_hebrew_year,
    )
    assert _year_label(person) == expected


@pytest.mark.parametrize(
    ["person_gender", "expected_gender", "expected_warning"],
    [
        [Person.Gender.MALE, Person.Gender.MALE, ""],
        [Person.Gender.FEMALE, Person.Gender.MALE, "wrong gender on file"],
        ["", Person.Gender.MALE, ""],
    ],
    ids=[
        "matching gender has no warning",
        "mismatched gender is flagged",
        "unknown gender is never flagged",
    ],
)
def test_gender_warning(family, person_gender, expected_gender, expected_warning):
    person = Person.objects.create(
        family=family, first_name_en="Test", last_name_en="Person", gender=person_gender
    )
    assert _gender_warning(person, expected_gender) == expected_warning


def _choice_value(field, person: Person):
    """ModelChoiceField's own iterator wraps each choice in a
    ModelChoiceIteratorValue carrying `.instance` - that's what
    PersonPickerSelect.create_option() actually receives at render time,
    not a bare pk (which is all field.prepare_value() would give back)."""
    return next(
        value
        for value, _label in field.choices
        if getattr(value, "instance", None) and value.instance.pk == person.pk
    )


def _father_option_attrs(form: PersonForm, person: Person) -> dict[str, str]:
    widget = form.fields["father"].widget
    assert isinstance(widget, PersonPickerSelect)
    value = _choice_value(form.fields["father"], person)
    option = widget.create_option("father", value, str(person), False, 0)
    return option["attrs"]


def test_person_picker_option_carries_display_data(family):
    father = Person.objects.create(
        family=family,
        first_name_en="Elchanan",
        last_name_en="Rokach",
        first_name_he="אלחנן",
        gender=Person.Gender.MALE,
        dob_gregorian=dt.date(1952, 1, 1),
    )
    form = PersonForm(family=family)

    attrs = _father_option_attrs(form, father)

    assert attrs["data-display-name"] == "Elchanan Rokach"
    assert attrs["data-birth-year"] == "b. 1952"
    assert attrs["data-hebrew-first-name"] == "אלחנן"
    assert "data-warning" not in attrs


def test_person_picker_option_flags_wrong_gender_on_file(family):
    wrong_gender_father = Person.objects.create(
        family=family, first_name_en="Blimi", last_name_en="Bernstein", gender=Person.Gender.FEMALE
    )
    # _parent_queryset only keeps a wrong-gender person in the choices when
    # they're already the *currently assigned* father - so the child here
    # needs to actually point at them, not just exist alongside them.
    child = Person.objects.create(
        family=family, first_name_en="Child", last_name_en="Test", father=wrong_gender_father
    )
    form = PersonForm(instance=child, family=family)

    attrs = _father_option_attrs(form, wrong_gender_father)

    assert attrs["data-warning"] == "wrong gender on file"


def test_person_picker_option_omits_hebrew_name_when_it_is_the_display_name(family):
    # A person with no English name falls back to their Hebrew first name
    # as display_name - the subtitle would just repeat it, so it's left off.
    person = Person.objects.create(family=family, first_name_he="אברהם", gender=Person.Gender.MALE)
    form = PersonForm(family=family)

    attrs = _father_option_attrs(form, person)

    assert "data-hebrew-first-name" not in attrs


def test_existing_spouse_picker_has_no_gender_warning(family):
    person_a = Person.objects.create(
        family=family, first_name_en="A", last_name_en="Test", gender=Person.Gender.MALE
    )
    spouse = Person.objects.create(
        family=family, first_name_en="B", last_name_en="Test", gender=Person.Gender.FEMALE
    )
    form = UnionForm(person_a=person_a, family=family)
    widget = form.fields["existing_spouse"].widget
    assert isinstance(widget, PersonPickerSelect)

    value = _choice_value(form.fields["existing_spouse"], spouse)
    option = widget.create_option("existing_spouse", value, str(spouse), False, 0)

    assert "data-warning" not in option["attrs"]
