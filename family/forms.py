from typing import Any

from django import forms
from django.db.models import QuerySet

from accounts.models import Account
from family.models import Person, Union
from family.widgets import (
    PersonPickerSelect,
    _parent_option_label,
    _person_option_label,
    prefetch_for_person_picker,
)
from tenants.models import Family, FamilyMembership


def _parent_queryset(
    candidates: QuerySet[Person], *, expected_gender: str, current_id: int | None, family: Family
) -> QuerySet[Person]:
    """Gender-filters candidates, but never drops whoever's already assigned.

    Otherwise editing a person whose father/mother field already holds
    bad data (wrong gender - see _parent_option_label - or, since a real
    incident, a cycle that predates Person.clean() rejecting one) would
    render that field blank, and saving the form would silently wipe it
    instead of leaving the bad-but-real data for a person to fix
    deliberately.

    `Q(pk=current_id)` alone isn't enough for this: it only *filters*
    `candidates`, so it can't resurrect a row `candidates` has already had
    excluded from it upstream (PersonForm.__init__ excludes the person's
    own descendants before calling this, to stop a *new* cycle - but that
    exclusion would just as happily hide an *existing* one). Explicitly
    unioning in a fresh fetch of `current_id` guarantees it survives
    regardless of what candidates already had removed - but that fresh
    fetch still has to be scoped to `family` itself, the same as
    `candidates` already is: a person can only be recorded as their own
    family's father/mother (see AGENTS.md), and an unscoped fetch here
    would cross that tenant boundary if `current_id` ever pointed at
    another family's person (a data bug, not something this should widen
    into a real leak of that person's name/gender into this family's
    picker).
    """
    filtered = candidates.filter(gender=expected_gender)
    if current_id is not None:
        filtered |= Person.objects.filter(pk=current_id, family=family)
    return filtered


class PersonForm(forms.ModelForm):
    """Adds Account/FamilyMembership fields on top of the plain Person form.

    Email/phone/family_role aren't Person fields - they're a thin
    front end onto an Account and a FamilyMembership. See the class-level
    save() docstring for the actual provisioning/linking rules.
    """

    email = forms.EmailField(required=False, help_text="Lets this person sign in with a magic link.")
    phone = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"type": "tel"}),
        help_text="Stored in E.164 format - the country/format picker handles that for you.",
    )
    family_role = forms.ChoiceField(
        choices=[("", "No access")] + list(FamilyMembership.Role.choices),
        required=False,
        initial=FamilyMembership.Role.MEMBER,
        label="Access to this family",
        help_text="Only takes effect once an email or phone is set above.",
    )

    class Meta:
        model = Person
        fields = [
            "first_name_en",
            "last_name_en",
            "first_name_he",
            "last_name_he",
            "nickname",
            "gender",
            "father",
            "mother",
            "notifications_enabled",
            "dob_gregorian",
            "dob_hebrew_year",
            "dob_hebrew_month",
            "dob_hebrew_day",
            "dob_year_only",
            "dod_gregorian",
            "dod_hebrew_year",
            "dod_hebrew_month",
            "dod_hebrew_day",
            "yahrzeit_adar_observance",
            "yahrzeit_day30_observance",
            "notes",
        ]
        widgets = {
            "dob_gregorian": forms.DateInput(attrs={"type": "date"}),
            "dod_gregorian": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, family: Family, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.family = family
        if not self.instance.pk:
            # Set on the instance immediately (not just at save time) so
            # it's already correct for the rest of the form's lifecycle.
            self.instance.family = family

        # A person can only be recorded as their own family's father/mother
        # - a parent who belongs to another family's ledger is left blank
        # here and only shown as a link on the child's profile.
        # prefetch_for_person_picker avoids an N+1 from _relations_hint's
        # own father/mother/spouse/children access, once per candidate in
        # the picker (a couple hundred people is a normal ledger size here).
        candidate_parents = prefetch_for_person_picker(Person.objects.filter(family=family))
        if self.instance.pk:
            excluded = self.instance.descendant_ids() | {self.instance.pk}
            candidate_parents = candidate_parents.exclude(pk__in=excluded)
        # Gender-filtered, not just "anyone in the family" - with a couple
        # hundred people in a ledger, an unfiltered list is unusable and
        # a father can't be female anyway.
        # Widget is set before queryset - ModelChoiceField.queryset's
        # setter is what actually populates widget.choices, so a widget
        # swapped in afterwards would be left with none.
        self.fields["father"].widget = PersonPickerSelect(expected_gender=Person.Gender.MALE)
        self.fields["mother"].widget = PersonPickerSelect(expected_gender=Person.Gender.FEMALE)
        self.fields["father"].queryset = _parent_queryset(
            candidate_parents,
            expected_gender=Person.Gender.MALE,
            current_id=self.instance.father_id,
            family=family,
        )
        self.fields["mother"].queryset = _parent_queryset(
            candidate_parents,
            expected_gender=Person.Gender.FEMALE,
            current_id=self.instance.mother_id,
            family=family,
        )
        self.fields["father"].required = False
        self.fields["mother"].required = False
        self.fields["father"].label_from_instance = _parent_option_label(Person.Gender.MALE)
        self.fields["mother"].label_from_instance = _parent_option_label(Person.Gender.FEMALE)

        # Once linked, the Account is the source of truth for contact
        # info - show its current values rather than a separate copy.
        if self.instance.account_id:
            account = self.instance.account
            self.fields["email"].initial = account.email
            self.fields["phone"].initial = account.phone
            membership = FamilyMembership.objects.filter(account=account, family=family).first()
            self.fields["family_role"].initial = membership.role if membership else ""

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        email = cleaned_data.get("email") or None
        phone = cleaned_data.get("phone") or None

        # Only resolve a match when this Person isn't linked yet - once
        # linked, editing these fields updates that same Account in
        # place rather than potentially re-pointing to a different one.
        self._matched_account = None
        if not self.instance.account_id and (email or phone):
            account = Account.objects.filter(email__iexact=email).first() if email else None
            if account is None and phone:
                account = Account.objects.filter(phone=phone).first()
            if account is not None:
                conflict = Person.objects.filter(family=self.family, account=account)
                if self.instance.pk:
                    conflict = conflict.exclude(pk=self.instance.pk)
                # Same-family conflict: someone else here already has this
                # contact. Cross-family conflict: this Account is already
                # someone else's real, tracked identity in a different
                # family - linking it here too would let save()'s "already
                # linked" branch overwrite that family's actual login
                # email/phone the next time this family edits it, and would
                # grant this family instant access to a stranger's account
                # with no consent from them. Both get the same treatment:
                # a visible error instead of a silent link.
                cross_family_conflict = Person.objects.filter(account=account).exclude(family=self.family)
                if conflict.exists():
                    self.add_error(
                        "email" if email else "phone",
                        "This contact is already linked to someone else in this family.",
                    )
                elif cross_family_conflict.exists():
                    self.add_error(
                        "email" if email else "phone",
                        "This contact is already tracked in another family.",
                    )
                else:
                    self._matched_account = account

        return cleaned_data

    def save(self, commit: bool = True) -> Person:
        """Saves the Person, then applies its Account provisioning rules.

        See the memory of the conversation that settled on these - not
        something to relitigate per edit:
        - Not yet linked + email/phone given: find-or-create an Account
          for that identifier and link it to this Person.
        - Already linked: the typed email/phone overwrite that Account's
          own fields directly (it's the source of truth) - never
          re-points to a different Account.
        - family_role only takes effect when there's an Account to grant
          it to; picking "No access" revokes any existing membership
          rather than doing nothing.
        """
        person = super().save(commit=commit)
        email = self.cleaned_data.get("email") or None
        phone = self.cleaned_data.get("phone") or None
        role = self.cleaned_data.get("family_role") or None

        account = person.account
        if account is None and (email or phone):
            account = self._matched_account or Account.objects.create_user(email=email, phone=phone)
            person.account = account
            person.save(update_fields=["account"])
        elif account is not None and (email or phone):
            account.email = email
            account.phone = phone
            account.save(update_fields=["email", "phone"])

        if account is not None:
            if role:
                FamilyMembership.objects.update_or_create(
                    account=account, family=self.family, defaults={"role": role}
                )
            else:
                FamilyMembership.objects.filter(account=account, family=self.family).delete()

        return person


class UnionForm(forms.ModelForm):
    """Adds a spouse to a person's record.

    The spouse is either someone already in this family's ledger, or a
    brand-new Person created on the spot - most spouses aren't already
    tracked, so that's the common path.
    """

    existing_spouse = forms.ModelChoiceField(
        queryset=Person.objects.none(),
        required=False,
        label="Already in the family",
    )
    new_spouse_first_name_en = forms.CharField(required=False, label="First name")
    new_spouse_last_name_en = forms.CharField(required=False, label="Last name")

    class Meta:
        model = Union
        fields = [
            "status",
            "marriage_date_gregorian",
            "marriage_hebrew_year",
            "marriage_hebrew_month",
            "marriage_hebrew_day",
        ]
        widgets = {
            "marriage_date_gregorian": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, person_a: Person, family: Family, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.person_a = person_a
        self.family = family
        candidates = prefetch_for_person_picker(Person.objects.filter(family=family).exclude(pk=person_a.pk))
        # Only excludes the *same* gender when person_a's own gender is
        # known - a blank/unknown gender on either side isn't reason
        # enough to hide a real candidate.
        if person_a.gender:
            candidates = candidates.exclude(gender=person_a.gender)
        # See the widget-before-queryset note in PersonForm.__init__.
        self.fields["existing_spouse"].widget = PersonPickerSelect()
        self.fields["existing_spouse"].queryset = candidates
        self.fields["existing_spouse"].label_from_instance = _person_option_label

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        existing = cleaned_data.get("existing_spouse")
        new_first = cleaned_data.get("new_spouse_first_name_en", "").strip()
        new_last = cleaned_data.get("new_spouse_last_name_en", "").strip()

        if existing and (new_first or new_last):
            raise forms.ValidationError(
                "Pick someone already in the family, or enter a new person's name - not both."
            )
        if not existing and not (new_first and new_last):
            raise forms.ValidationError(
                "Pick someone already in the family, or enter a new person's first and last name."
            )

        # Set on the instance now (not in save()) so it's already correct
        # by the time ModelForm._post_clean() calls instance.full_clean() -
        # otherwise Union.clean()'s "not the same person" check compares
        # two still-unset fields and always raises. The new-person case
        # gets an unsaved Person here (not .create()'d) so validation
        # never has a database side effect - it's persisted for real in
        # save() instead.
        self.instance.person_a = self.person_a
        if existing:
            self.instance.person_b = existing
        else:
            self.instance.person_b = Person(
                family=self.family, first_name_en=new_first, last_name_en=new_last
            )

        return cleaned_data

    def save(self, commit: bool = True) -> Union:
        if not self.instance.person_b.pk:
            self.instance.person_b.save()
        return super().save(commit=commit)


class UnionEditForm(forms.ModelForm):
    """Edits an existing marriage's own details.

    Who the two people are doesn't change here, only the marriage's own
    details.
    """

    class Meta:
        model = Union
        fields = [
            "status",
            "marriage_date_gregorian",
            "marriage_hebrew_year",
            "marriage_hebrew_month",
            "marriage_hebrew_day",
            "divorce_date_gregorian",
        ]
        widgets = {
            "marriage_date_gregorian": forms.DateInput(attrs={"type": "date"}),
            "divorce_date_gregorian": forms.DateInput(attrs={"type": "date"}),
        }
