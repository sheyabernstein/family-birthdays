import datetime as dt

import pytest

from family.forms import PersonForm, UnionForm
from family.models import Person, Union
from family.widgets import (
    PersonPickerSelect,
    _children_hint,
    _gender_warning,
    _parents_hint,
    _person_option_label,
    _relations_hint,
    _spouse_hint,
    _year_label,
    prefetch_for_person_picker,
)

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


def test_parents_hint_names_both_parents(family):
    father = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    mother = Person.objects.create(family=family, first_name_en="Bruchele", last_name_en="Rokach")
    person = Person.objects.create(
        family=family, first_name_en="Blimi", last_name_en="Rokach", father=father, mother=mother
    )

    assert _parents_hint(person) == "child of Shloime Rokach & Bruchele Rokach"


def test_parents_hint_includes_a_deceased_or_untracked_parent(family):
    # Unlike Person.parents_label (warm notification copy), this is for
    # telling two ledger entries apart - an untracked/deceased parent's
    # name is exactly the useful clue, not something to hide.
    father = Person.objects.create(
        family=family, first_name_en="Shloime", last_name_en="Rokach", notifications_enabled=False
    )
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach", father=father)

    assert _parents_hint(person) == "child of Shloime Rokach"


def test_parents_hint_is_empty_without_any_recorded_parent(family):
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")

    assert _parents_hint(person) == ""


def test_spouse_hint_names_a_married_spouse_from_either_side(family):
    person_a = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    person_b = Person.objects.create(family=family, first_name_en="Bruchele", last_name_en="Rokach")
    Union.objects.create(person_a=person_a, person_b=person_b, status=Union.Status.MARRIED)

    assert _spouse_hint(person_a) == "spouse of Bruchele Rokach"
    assert _spouse_hint(person_b) == "spouse of Shloime Rokach"


def test_spouse_hint_omits_a_non_married_union(family):
    # _spouse_hint itself doesn't filter by status - it relies on
    # prefetch_for_person_picker's own married-only Prefetch already
    # having scoped unions_as_a/_b down before this reads them.
    person_a = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    person_b = Person.objects.create(family=family, first_name_en="Bruchele", last_name_en="Rokach")
    Union.objects.create(person_a=person_a, person_b=person_b, status=Union.Status.DIVORCED)
    person_a = prefetch_for_person_picker(Person.objects.filter(pk=person_a.pk)).get()

    assert _spouse_hint(person_a) == ""


def test_spouse_hint_is_empty_without_any_union(family):
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")

    assert _spouse_hint(person) == ""


def test_children_hint_names_children_from_either_parent_field(family):
    parent = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach", father=parent)
    Person.objects.create(family=family, first_name_en="Elchanan", last_name_en="Rokach", mother=parent)

    assert _children_hint(parent) == "parent of Blimi Rokach, Elchanan Rokach"


def test_children_hint_is_empty_without_any_recorded_child(family):
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")

    assert _children_hint(person) == ""


def test_relations_hint_combines_parent_spouse_and_child_fragments(family):
    grandparent = Person.objects.create(family=family, first_name_en="Yitzchok", last_name_en="Rokach")
    spouse = Person.objects.create(family=family, first_name_en="Bruchele", last_name_en="Rokach")
    person = Person.objects.create(
        family=family, first_name_en="Shloime", last_name_en="Rokach", father=grandparent
    )
    Union.objects.create(person_a=person, person_b=spouse, status=Union.Status.MARRIED)
    Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach", father=person)

    assert _relations_hint(person) == (
        "child of Yitzchok Rokach; spouse of Bruchele Rokach; parent of Blimi Rokach"
    )


def test_relations_hint_is_empty_without_any_relation_on_file(family):
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")

    assert _relations_hint(person) == ""


def test_person_option_label_includes_the_parents_hint(family):
    father = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach", father=father)

    assert _person_option_label(person) == "Blimi Rokach (birth year unknown) - child of Shloime Rokach"


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


def test_person_picker_option_carries_the_relations_hint(family):
    grandfather = Person.objects.create(family=family, first_name_en="Yitzchok", last_name_en="Rokach")
    father = Person.objects.create(
        family=family,
        first_name_en="Elchanan",
        last_name_en="Rokach",
        gender=Person.Gender.MALE,
        father=grandfather,
    )
    form = PersonForm(family=family)

    attrs = _father_option_attrs(form, father)

    assert attrs["data-relations-hint"] == "child of Yitzchok Rokach"


def test_person_picker_option_omits_the_relations_hint_without_any_recorded_relation(family):
    father = Person.objects.create(
        family=family, first_name_en="Elchanan", last_name_en="Rokach", gender=Person.Gender.MALE
    )
    form = PersonForm(family=family)

    attrs = _father_option_attrs(form, father)

    assert "data-relations-hint" not in attrs


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
