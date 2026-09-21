import uuid
from collections.abc import Callable

import reversion
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from hdate import HebrewDate
from hdate.hebrew_date import Months

from family.hebrew import format_hebrew_date, hebrew_to_gregorian

HEBREW_MONTH_CHOICES = [(m.value, m.name.replace("_", " ").title()) for m in Months]


def bfs_relative_ids(seed_ids: set[int], expand: Callable[[set[int]], set[int]]) -> set[int]:
    """Breadth-first traversal: repeatedly expands a frontier via `expand` until it's exhausted.

    Shared by Person.descendant_ids() (frontier expands to children) and
    notifications.audience's ancestor lookup (frontier expands to
    parents) - same accumulate-until-empty shape either way, one query
    per generation; only what counts as "next frontier" differs, which
    is exactly what `expand` captures.

    Args:
        seed_ids: The starting frontier - typically one person's own id.
        expand: Given the current frontier, returns the next one (already
            expected to exclude ids already seen - the caller's query is
            usually cheaper written that way than filtering here).
    """
    ids: set[int] = set()
    frontier = set(seed_ids)
    while frontier:
        frontier = expand(frontier) - ids
        ids |= frontier
    return ids


class AdarObservance(models.TextChoices):
    ADAR_I = "adar_i", "Adar I"
    ADAR_II = "adar_ii", "Adar II (default)"


class Day30Observance(models.TextChoices):
    NEXT_MONTH = "start_of_next_month", "1st of following month (default)"
    LAST_DAY = "last_day_of_month", "Last day of the (now 29-day) month"


@reversion.register()
class Person(models.Model):
    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"

    # Used in URLs instead of the database pk, so a person's id in the app
    # can't be enumerated or reused as a hint about record count/order.
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    family = models.ForeignKey(
        "tenants.Family",
        on_delete=models.CASCADE,
        related_name="people",
        help_text="The family ledger this record belongs to. In-laws from a Union may belong to a different family.",
    )
    # A ForeignKey, not OneToOne: the same login identity can be tracked
    # as a Person in more than one family (e.g. an in-law has their own
    # row in their birth family's ledger too), and both should share one
    # Account rather than needing separate logins.
    account = models.ForeignKey(
        "accounts.Account",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="people",
        help_text="If this person has their own login, the account that is them.",
    )

    first_name_en = models.CharField(max_length=100, verbose_name="first name (English)")
    last_name_en = models.CharField(max_length=100, verbose_name="last name (English)")
    first_name_he = models.CharField(max_length=100, blank=True, verbose_name="first name (Hebrew)")
    last_name_he = models.CharField(max_length=100, blank=True, verbose_name="last name (Hebrew)")
    nickname = models.CharField(max_length=100, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)

    father = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children_as_father"
    )
    mother = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children_as_mother"
    )

    # Computed on save from whether a death date is recorded - not user
    # editable. Unlike the Hebrew/Gregorian date pair, there's no judgment
    # call here for a form to make: a death date means not living, no
    # death date means living, full stop.
    is_living = models.BooleanField(default=True, editable=False)

    # Date of birth, both calendars - plain storage, nothing computed here.
    # Whichever form creates/edits a Person is responsible for working out
    # a missing half from the other (see family.hebrew) and for asking
    # whether the birth was after sunset, since a Hebrew date derived from
    # only the Gregorian date is wrong by one day in that case.
    dob_gregorian = models.DateField(
        null=True,
        blank=True,
        verbose_name="date of birth (Gregorian)",
        help_text="Civil calendar date. Enter directly if known.",
    )
    dob_hebrew_year = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Hebrew birth year",
        help_text=(
            "Hebrew year. Enter this directly if known, rather than letting it be computed from the "
            "Gregorian date - especially if the birth was after sunset, when the Hebrew date has "
            "already advanced to the next day."
        ),
    )
    dob_hebrew_month = models.PositiveSmallIntegerField(
        choices=HEBREW_MONTH_CHOICES,
        null=True,
        blank=True,
        verbose_name="Hebrew birth month",
        help_text="Hebrew month.",
    )
    dob_hebrew_day = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Hebrew birth day", help_text="Hebrew day of the month."
    )
    dob_year_only = models.BooleanField(
        default=False, verbose_name="year of birth only", help_text="Only the year of birth is known"
    )

    # Date of death, same shape - and the sunset caveat matters even more
    # here, since the Hebrew date is what yahrzeit observance is based on.
    dod_gregorian = models.DateField(
        null=True,
        blank=True,
        verbose_name="date of death (Gregorian)",
        help_text="Civil calendar date. Enter directly if known.",
    )
    dod_hebrew_year = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Hebrew death year",
        help_text=(
            "Hebrew year. Enter this directly if known - e.g. from the yahrzeit already observed for "
            "this person - rather than letting it be computed from the Gregorian date. This matters "
            "even more here than for birth dates: if death was after sunset, the Hebrew date has "
            "already advanced to the next day, and getting it wrong shifts the yahrzeit itself."
        ),
    )
    dod_hebrew_month = models.PositiveSmallIntegerField(
        choices=HEBREW_MONTH_CHOICES,
        null=True,
        blank=True,
        verbose_name="Hebrew death month",
        help_text="Hebrew month.",
    )
    dod_hebrew_day = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Hebrew death day", help_text="Hebrew day of the month."
    )

    # Per-person overrides for the halachic ambiguities in yahrzeit dates.
    # Defaults reflect common Ashkenazi custom; some family branches will
    # differ and need to override these explicitly.
    yahrzeit_adar_observance = models.CharField(
        max_length=10,
        choices=AdarObservance.choices,
        default=AdarObservance.ADAR_II,
        verbose_name="yahrzeit Adar observance",
    )
    yahrzeit_day30_observance = models.CharField(
        max_length=20,
        choices=Day30Observance.choices,
        default=Day30Observance.NEXT_MONTH,
        verbose_name="yahrzeit 30-day observance",
    )

    # Off for someone recorded for lineage only - e.g. an in-law's own
    # parent, who isn't part of this family and shouldn't get birthday/
    # yahrzeit occurrences computed or sent just because their child
    # married in.
    notifications_enabled = models.BooleanField(default=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # pk tiebreaker - duplicate names are the norm here, not the
        # exception (see AGENTS.md's "three Blimi Rokachs" example).
        ordering = ["last_name_en", "first_name_en", "pk"]
        constraints = [
            # A given Account should only ever represent one Person within
            # a single family's ledger - two Persons here sharing a login
            # would make "who am I" ambiguous for that account. The same
            # Account linking to a *different* family's Person is fine and
            # expected (e.g. an in-law tracked in two ledgers).
            models.UniqueConstraint(fields=["family", "account"], name="person_account_unique_per_family"),
        ]

    def __str__(self) -> str:
        return self.display_name

    def save(self, *args, **kwargs) -> None:
        self.is_living = not bool(self.dod_gregorian or self.dod_hebrew_year)
        super().save(*args, **kwargs)

    @property
    def display_name(self) -> str:
        return self.nickname or f"{self.first_name_en} {self.last_name_en}".strip() or self.hebrew_name or "?"

    @property
    def parents_label(self) -> str | None:
        """A short "Parent & Parent's FirstName" label - the way people actually get told apart.

        Shown on every occurrence notification (see
        notifications/templates/notifications/email/) - not just when
        this person's name happens to collide with someone else's,
        since it reads as a warm, personal touch either way, the same
        way you'd actually refer to someone in a large family
        conversationally ("oh, Shloime & Bruchele's Blimi"). It's
        especially valuable once names repeat - large families
        following the custom of naming children after grandparents
        produce a lot of exact repeats (e.g. three different "Blimi
        Rokach"s, each with completely different parents, seen for real
        in this app's own data) - but there's no reason to withhold it
        the rest of the time.

        Only *living*, *tracked* parents are used - an untracked parent
        (`notifications_enabled=False`, e.g. an in-law's own parent,
        entered only so the tree renders - see that field's own
        docstring) is a lineage-only stub outside the family's actual
        active sphere, and naming one in an otherwise personal
        notification would read as confusing ("who's that?") rather
        than helpful. First names (or nicknames) only, for both the
        parents and this person - the surname is already given in full
        wherever this label is shown, so repeating it three times in
        one short phrase ("Shloime Rokach & Bruchele Rokach's Blimi
        Rokach") would be the opposite of concise.
        """
        living_parent_names = [
            p.nickname or p.first_name_en
            for p in (self.father, self.mother)
            if p is not None and p.is_living and p.notifications_enabled
        ]
        if not living_parent_names:
            return None
        parents = " & ".join(living_parent_names)
        return f"{parents}'s {self.nickname or self.first_name_en}"

    @property
    def patronymic_label(self) -> str | None:
        """The traditional yahrzeit naming form: Hebrew first name, בן/בת, father's Hebrew first name.

        Patronymic only, never the mother's name - the traditional form
        used for a yahrzeit/kaddish, deliberately narrower than
        parents_label's own both-parents convention (see AGENTS.md).
        Unlike parents_label, the father's name is used regardless of
        is_living/notifications_enabled - the whole point of this label
        is naming a real ancestor even when they're only a lineage stub
        (see EventType.always_schedule), not just a living, tracked
        relative. Returns None when either this person's own or the
        father's Hebrew first name isn't recorded - there's nothing
        accurate to construct otherwise.
        """
        if not self.first_name_he or self.father is None or not self.father.first_name_he:
            return None
        connector = "בת" if self.gender == Person.Gender.FEMALE else "בן"
        return f"{self.first_name_he} {connector} {self.father.first_name_he}"

    @property
    def dob_hebrew_anchor(self) -> tuple[Months, int] | None:
        if self.dob_hebrew_month and self.dob_hebrew_day:
            return Months(self.dob_hebrew_month), self.dob_hebrew_day
        return None

    @property
    def dod_hebrew_anchor(self) -> tuple[Months, int] | None:
        if self.dod_hebrew_month and self.dod_hebrew_day:
            return Months(self.dod_hebrew_month), self.dod_hebrew_day
        return None

    @property
    def dob_hebrew_display(self) -> str | None:
        if self.dob_hebrew_year and self.dob_hebrew_month and self.dob_hebrew_day:
            return format_hebrew_date(
                HebrewDate(self.dob_hebrew_year, Months(self.dob_hebrew_month), self.dob_hebrew_day)
            )
        return None

    @property
    def dob_hebrew_month_day_display(self) -> str | None:
        """Same as dob_hebrew_display, but never reveals the birth year.

        For a viewer who shouldn't see this person's birth year (see
        family.access.can_see_birth_year) but can still reasonably see
        which day to wish them a happy birthday on. The real
        dob_hebrew_year is still required to construct a valid
        HebrewDate (leap-year/month-length rules need a real year) - it's
        just never read into the rendered string.
        """
        if self.dob_hebrew_year and self.dob_hebrew_month and self.dob_hebrew_day:
            return format_hebrew_date(
                HebrewDate(self.dob_hebrew_year, Months(self.dob_hebrew_month), self.dob_hebrew_day),
                include_year=False,
            )
        return None

    @property
    def dod_hebrew_display(self) -> str | None:
        if self.dod_hebrew_year and self.dod_hebrew_month and self.dod_hebrew_day:
            return format_hebrew_date(
                HebrewDate(self.dod_hebrew_year, Months(self.dod_hebrew_month), self.dod_hebrew_day)
            )
        return None

    @property
    def hebrew_name(self) -> str | None:
        name = f"{self.first_name_he} {self.last_name_he}".strip()
        return name or None

    @property
    def age(self) -> float | None:
        if not self.dob_gregorian:
            return None
        return (timezone.localdate() - self.dob_gregorian).days / 365

    def clean(self) -> None:
        if self.father_id and self.father_id == self.pk:
            raise ValidationError("A person cannot be their own father.")
        if self.mother_id and self.mother_id == self.pk:
            raise ValidationError("A person cannot be their own mother.")
        # A deeper cycle (A's mother is B, B's mother is A) is just as
        # invalid as direct self-parenting above, and isn't hypothetical -
        # this happened for real: a new person was created as an existing
        # person's parent, and that same new person's own (unrelated)
        # parent field was mistakenly filled in with the person they were
        # just added as the parent of, closing the loop across two saves.
        # self.pk is None for a not-yet-created person, for whom
        # descendant_ids() is always correctly empty (they can't have
        # descendants before they exist) - no separate guard needed.
        if self.pk:
            descendants = self.descendant_ids()
            if self.father_id and self.father_id in descendants:
                raise ValidationError(
                    "This person's father can't be one of their own descendants - "
                    "that would make them their own ancestor."
                )
            if self.mother_id and self.mother_id in descendants:
                raise ValidationError(
                    "This person's mother can't be one of their own descendants - "
                    "that would make them their own ancestor."
                )
        if self.dob_gregorian and self.dod_gregorian and self.dod_gregorian < self.dob_gregorian:
            raise ValidationError("Date of death cannot be before date of birth.")

    def descendant_ids(self) -> set[int]:
        """Returns the ids of every descendant, via BFS over children_as_father/children_as_mother.

        Used to keep PersonForm's father/mother pickers from offering
        someone's own descendant as their parent, which would make them
        their own ancestor.
        """

        def _children(frontier: set[int]) -> set[int]:
            return set(
                Person.objects.filter(
                    models.Q(father_id__in=frontier) | models.Q(mother_id__in=frontier)
                ).values_list("pk", flat=True)
            )

        return bfs_relative_ids({self.pk}, _children)


@reversion.register()
class Union(models.Model):
    class Status(models.TextChoices):
        MARRIED = "married", "Married"
        DIVORCED = "divorced", "Divorced"
        WIDOWED = "widowed", "Widowed"

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    person_a = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="unions_as_a")
    person_b = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="unions_as_b")
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.MARRIED,
        help_text="Use married for upcoming weddings",
    )

    marriage_date_gregorian = models.DateField(
        null=True,
        blank=True,
        verbose_name="marriage date (Gregorian)",
        help_text="Civil calendar date. Enter directly if known.",
    )
    marriage_hebrew_year = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Hebrew marriage year",
        help_text=(
            "Hebrew year. Enter this directly if known, rather than letting it be computed from the "
            "Gregorian date - especially if the wedding was after sunset, when the Hebrew date has "
            "already advanced to the next day."
        ),
    )
    marriage_hebrew_month = models.PositiveSmallIntegerField(
        choices=HEBREW_MONTH_CHOICES,
        null=True,
        blank=True,
        verbose_name="Hebrew marriage month",
        help_text="Hebrew month.",
    )
    marriage_hebrew_day = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Hebrew marriage day", help_text="Hebrew day of the month."
    )

    divorce_date_gregorian = models.DateField(null=True, blank=True, verbose_name="divorce date (Gregorian)")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(person_a=models.F("person_b")), name="union_distinct_people"
            ),
        ]
        # No timestamp field on this model - mirrors Person's own
        # ordering rather than adding one just for this. person_b breaks
        # ties on person_a's own name before falling back to pk.
        ordering = [
            "person_a__last_name_en",
            "person_a__first_name_en",
            "person_b__last_name_en",
            "person_b__first_name_en",
            "pk",
        ]

    def __str__(self) -> str:
        return f"{self.person_a} & {self.person_b}"

    @property
    def marriage_hebrew_anchor(self) -> tuple[Months, int] | None:
        if self.marriage_hebrew_month and self.marriage_hebrew_day:
            return Months(self.marriage_hebrew_month), self.marriage_hebrew_day
        return None

    @property
    def marriage_hebrew_display(self) -> str | None:
        if self.marriage_hebrew_year and self.marriage_hebrew_month and self.marriage_hebrew_day:
            return format_hebrew_date(
                HebrewDate(
                    self.marriage_hebrew_year, Months(self.marriage_hebrew_month), self.marriage_hebrew_day
                )
            )
        return None

    def other(self, person: Person) -> Person:
        return self.person_b if person.pk == self.person_a_id else self.person_a

    @property
    def is_upcoming(self) -> bool:
        """True for a recorded-as-married Union whose wedding day itself hasn't happened yet.

        There's deliberately no separate "engaged" status to flip to
        "married" later (see AGENTS.md) - a future marriage_date is what
        an engagement *is* here, so this is always computed, never
        stored. The Hebrew anchor is only converted for this one-off
        runtime comparison, never persisted, so it doesn't run afoul of
        the never-derive-one-calendar-from-the-other rule.
        """
        if self.status != Union.Status.MARRIED:
            return False
        today = timezone.localdate()
        if self.marriage_date_gregorian:
            return self.marriage_date_gregorian > today
        anchor = self.marriage_hebrew_anchor
        if anchor and self.marriage_hebrew_year:
            month, day = anchor
            return hebrew_to_gregorian(HebrewDate(self.marriage_hebrew_year, month, day)) > today
        return False

    def clean(self) -> None:
        if self.person_a_id == self.person_b_id:
            raise ValidationError("A person cannot be in a union with themselves.")
