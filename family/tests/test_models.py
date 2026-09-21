import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from family.models import Person, Union

pytestmark = pytest.mark.django_db


def test_person_stores_gregorian_and_hebrew_dob_independently(family):
    # The model is pure storage - it doesn't derive one calendar from the
    # other. That logic (and the after-sunset judgment call) belongs to
    # whatever form creates the Person, not the model.
    person = Person.objects.create(
        family=family, first_name_en="Test", last_name_en="Person", dob_gregorian=dt.date(1990, 9, 22)
    )
    assert person.dob_gregorian == dt.date(1990, 9, 22)
    assert person.dob_hebrew_year is None


def test_explicit_hebrew_dob_is_stored_as_given(family):
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_gregorian=dt.date(1990, 9, 22),
        dob_hebrew_year=5750,
        dob_hebrew_month=1,
        dob_hebrew_day=1,
    )
    assert person.dob_hebrew_year == 5750


def test_death_before_birth_is_rejected(family):
    person = Person(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_gregorian=dt.date(1990, 1, 1),
        dod_gregorian=dt.date(1980, 1, 1),
    )
    with pytest.raises(ValidationError):
        person.clean()


def test_is_living_defaults_true_and_flips_once_a_death_date_is_saved(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    assert person.is_living is True

    person.dod_gregorian = dt.date(2020, 1, 1)
    person.save()
    assert person.is_living is False


def test_is_living_flips_from_a_hebrew_only_death_date(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")

    person.dod_hebrew_year = 5780
    person.dod_hebrew_month = 1
    person.dod_hebrew_day = 1
    person.save()

    assert person.is_living is False


def test_is_living_cannot_be_set_directly_it_is_recomputed_on_save(family):
    # Setting it manually doesn't stick - save() always recomputes it from
    # whether a death date is present, so the two can never drift apart.
    person = Person.objects.create(
        family=family, first_name_en="Test", last_name_en="Person", is_living=False
    )
    assert person.is_living is True


def test_person_cannot_be_their_own_father(family):
    person = Person.objects.create(family=family, first_name_en="Test", last_name_en="Person")
    person.father_id = person.pk
    with pytest.raises(ValidationError):
        person.clean()


def test_person_cannot_have_their_own_child_as_a_parent(family):
    # A two-node cycle (A's mother is B, B's mother is A) is just as
    # invalid as direct self-parenting above - a real incident, not
    # hypothetical (see AGENTS.md/family/views.PersonCreateView).
    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", mother=grandparent
    )

    grandparent.mother_id = parent.pk
    with pytest.raises(ValidationError):
        grandparent.clean()


def test_person_cannot_have_a_more_distant_descendant_as_a_parent(family):
    grandparent = Person.objects.create(family=family, first_name_en="G", last_name_en="Person")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Person", father=grandparent
    )
    grandchild = Person.objects.create(
        family=family, first_name_en="Grandchild", last_name_en="Person", father=parent
    )

    grandparent.father_id = grandchild.pk
    with pytest.raises(ValidationError):
        grandparent.clean()


def test_person_can_have_an_unrelated_parent_with_no_descendants_in_common(family):
    # Sanity check: the new cycle check shouldn't false-positive on an
    # ordinary, valid parent assignment.
    child = Person.objects.create(family=family, first_name_en="Child", last_name_en="Person")
    unrelated = Person.objects.create(family=family, first_name_en="Unrelated", last_name_en="Person")

    child.father_id = unrelated.pk
    child.clean()  # does not raise


def test_a_brand_new_unsaved_person_has_no_descendants_to_conflict_with(family):
    # self.pk is None here - descendant_ids() must not be called in a way
    # that errors for a not-yet-created person (who trivially has none).
    parent = Person.objects.create(family=family, first_name_en="Parent", last_name_en="Person")
    new_person = Person(family=family, first_name_en="New", last_name_en="Person", father=parent)

    new_person.clean()  # does not raise


def test_dob_hebrew_display_omits_the_thousands_digit(family):
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_hebrew_year=5786,
        dob_hebrew_month=6,
        dob_hebrew_day=14,
    )
    assert person.dob_hebrew_display == 'י"ד אדר תשפ"ו'


def test_dob_hebrew_month_day_display_omits_the_year_entirely(family):
    person = Person.objects.create(
        family=family,
        first_name_en="Test",
        last_name_en="Person",
        dob_hebrew_year=5786,
        dob_hebrew_month=6,
        dob_hebrew_day=14,
    )
    assert person.dob_hebrew_month_day_display == 'י"ד אדר'


def test_parents_label_shows_even_without_a_naming_collision(family):
    father = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    person = Person.objects.create(
        family=family, first_name_en="Unique", last_name_en="Person", father=father
    )
    assert person.parents_label == "Shloime's Unique"


def test_parents_label_uses_living_parents_first_names_only(family):
    father = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    mother = Person.objects.create(family=family, first_name_en="Bruchele", last_name_en="Rokach")
    person = Person.objects.create(
        family=family, first_name_en="Blimi", last_name_en="Rokach", father=father, mother=mother
    )

    assert person.parents_label == "Shloime & Bruchele's Blimi"


def test_parents_label_omits_a_deceased_parent(family):
    living_father = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    deceased_mother = Person.objects.create(
        family=family, first_name_en="Bruchele", last_name_en="Rokach", dod_gregorian=dt.date(2020, 1, 1)
    )
    person = Person.objects.create(
        family=family,
        first_name_en="Blimi",
        last_name_en="Rokach",
        father=living_father,
        mother=deceased_mother,
    )

    assert person.parents_label == "Shloime's Blimi"


def test_parents_label_omits_an_untracked_parent(family):
    # notifications_enabled=False - a lineage-only stub outside the
    # family's actual active sphere (e.g. an in-law's own parent), not
    # someone whose name should surface in an otherwise personal
    # notification. See Person.notifications_enabled's own docstring.
    living_father = Person.objects.create(family=family, first_name_en="Shloime", last_name_en="Rokach")
    untracked_mother = Person.objects.create(
        family=family, first_name_en="Bruchele", last_name_en="Rokach", notifications_enabled=False
    )
    person = Person.objects.create(
        family=family,
        first_name_en="Blimi",
        last_name_en="Rokach",
        father=living_father,
        mother=untracked_mother,
    )

    assert person.parents_label == "Shloime's Blimi"


def test_parents_label_prefers_a_parents_nickname(family):
    father = Person.objects.create(
        family=family, first_name_en="Shloime", last_name_en="Rokach", nickname="Shloimy"
    )
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach", father=father)

    assert person.parents_label == "Shloimy's Blimi"


def test_parents_label_is_none_without_any_recorded_parent(family):
    person = Person.objects.create(family=family, first_name_en="Blimi", last_name_en="Rokach")

    assert person.parents_label is None


def test_parents_label_is_none_when_the_only_recorded_parent_is_deceased(family):
    deceased_father = Person.objects.create(
        family=family, first_name_en="Shloime", last_name_en="Rokach", dod_gregorian=dt.date(2020, 1, 1)
    )
    person = Person.objects.create(
        family=family, first_name_en="Blimi", last_name_en="Rokach", father=deceased_father
    )

    assert person.parents_label is None


def test_patronymic_label_uses_gendered_connector(family):
    father = Person.objects.create(family=family, first_name_he="אברהם", last_name_en="Rokach")
    son = Person.objects.create(
        family=family, first_name_he="יעקב", last_name_en="Rokach", father=father, gender=Person.Gender.MALE
    )
    daughter = Person.objects.create(
        family=family,
        first_name_he="רבקה",
        last_name_en="Rokach",
        father=father,
        gender=Person.Gender.FEMALE,
    )

    assert son.patronymic_label == "יעקב בן אברהם"
    assert daughter.patronymic_label == "רבקה בת אברהם"


def test_patronymic_label_ignores_untracked_and_deceased_status(family):
    # Unlike parents_label, the whole point here is naming a real
    # ancestor even when they're only a lineage stub or no longer living
    # - see EventType.always_schedule and AGENTS.md.
    father = Person.objects.create(
        family=family,
        first_name_he="אברהם",
        last_name_en="Rokach",
        notifications_enabled=False,
        dod_gregorian=dt.date(2020, 1, 1),
    )
    person = Person.objects.create(family=family, first_name_he="יעקב", last_name_en="Rokach", father=father)

    assert person.patronymic_label == "יעקב בן אברהם"


def test_patronymic_label_is_none_without_a_hebrew_first_name(family):
    father = Person.objects.create(family=family, first_name_he="אברהם", last_name_en="Rokach")
    person = Person.objects.create(family=family, first_name_en="Jacob", last_name_en="Rokach", father=father)

    assert person.patronymic_label is None


def test_patronymic_label_is_none_without_the_fathers_hebrew_first_name(family):
    father = Person.objects.create(family=family, first_name_en="Avraham", last_name_en="Rokach")
    person = Person.objects.create(family=family, first_name_he="יעקב", last_name_en="Rokach", father=father)

    assert person.patronymic_label is None


def test_patronymic_label_is_none_without_a_recorded_father(family):
    person = Person.objects.create(family=family, first_name_he="יעקב", last_name_en="Rokach")

    assert person.patronymic_label is None


def test_display_name_prefers_nickname(family):
    person = Person(family=family, first_name_en="Robert", last_name_en="Smith", nickname="Bobby")
    assert person.display_name == "Bobby"


def test_descendant_ids_includes_grandchildren_but_not_unrelated_people(family):
    grandparent = Person.objects.create(family=family, first_name_en="Grandparent", last_name_en="Rokach")
    parent = Person.objects.create(
        family=family, first_name_en="Parent", last_name_en="Rokach", father=grandparent
    )
    child = Person.objects.create(family=family, first_name_en="Child", last_name_en="Rokach", mother=parent)
    Person.objects.create(family=family, first_name_en="Unrelated", last_name_en="Person")

    assert grandparent.descendant_ids() == {parent.pk, child.pk}
    assert parent.descendant_ids() == {child.pk}
    assert child.descendant_ids() == set()


@pytest.mark.parametrize(
    ["marriage_days_offset", "status", "expected"],
    [
        [30, Union.Status.MARRIED, True],
        [-30, Union.Status.MARRIED, False],
        [None, Union.Status.MARRIED, False],
        [30, Union.Status.DIVORCED, False],
    ],
    ids=[
        "upcoming when the marriage date is in the future",
        "not upcoming once the marriage date has passed",
        "not upcoming without a marriage date",
        "not upcoming once divorced, even with a future date - an edited/corrected record, not a real scenario",
    ],
)
def test_union_is_upcoming(family, marriage_days_offset, status, expected):
    person_a = Person.objects.create(family=family, first_name_en="A", last_name_en="Test")
    person_b = Person.objects.create(family=family, first_name_en="B", last_name_en="Test")
    marriage_date = (
        timezone.localdate() + dt.timedelta(days=marriage_days_offset)
        if marriage_days_offset is not None
        else None
    )
    union = Union.objects.create(
        person_a=person_a, person_b=person_b, status=status, marriage_date_gregorian=marriage_date
    )

    assert union.is_upcoming is expected
