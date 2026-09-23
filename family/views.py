import datetime as dt
import itertools
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import dateformat, timezone
from django.utils.http import urlencode
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView
from hdate import HebrewDate
from hdate.hebrew_date import Months

from family.access import can_see_birth_year, person_is_visible, visible_people_queryset
from family.forms import PersonForm, UnionEditForm, UnionForm
from family.hebrew import gregorian_to_hebrew, hebrew_to_gregorian
from family.history import person_history
from family.models import Person, Union
from family.tree_chart import build_chart_data
from notifications.audience import PreferenceResolver, available_channels
from notifications.helpers import channel_rows
from notifications.models import EventType, Occurrence
from notifications.tasks import person_has_passed_coming_of_age, union_is_eligible_for_notifications
from tenants.mixins import (
    FamilyEditorRequiredMixin,
    FamilyOwnerRequiredMixin,
    FamilyRequiredMixin,
    FamilyScopedMixin,
)
from tenants.models import Family, FamilyMembership


class HelpView(LoginRequiredMixin, TemplateView):
    """Reference documentation, not an onboarding flow.

    Reachable any time from the nav, not shown automatically on first
    login. Login-required only (not FamilyRequiredMixin), since it needs
    to be usable before someone has created or joined their first family -
    exactly when the "everyone" section matters most. The owner/editor
    section is gated the same way every other role-specific block in this
    app is (request.family_role is None, not an error, when there's no
    current family - see CurrentFamilyMiddleware).
    """

    template_name = "family/help.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        if self.request.family is not None:
            context["family_owners"] = _family_owner_contacts(self.request.family)
        return context


def _family_owner_contacts(family: Family) -> list[dict[str, str | None]]:
    """Who to contact about a workspace: its owner(s), by name and email.

    Email only, never phone - a member reaching out about a family/tenant
    problem (a role that needs changing, a record only an owner can
    delete) is exactly the kind of contact that shouldn't assume SMS is
    even set up. Named via the owner's own Person record in this family
    when one exists (the name people actually recognize them by), falling
    back to their account email when it doesn't (e.g. an owner who hasn't
    been added to their own family's tree).
    """
    memberships = (
        FamilyMembership.objects.filter(family=family, role=FamilyMembership.Role.OWNER)
        .select_related("account")
        .order_by("account__email")
    )
    contacts = []
    for membership in memberships:
        account = membership.account
        person = family.people.filter(account=account).first()
        contacts.append(
            {
                "name": person.display_name if person else (account.email or "Owner"),
                "email": account.email,
            }
        )
    return contacts


class GregorianToHebrewView(LoginRequiredMixin, View):
    """AJAX-only: converts a Gregorian date to its Hebrew equivalent.

    Lets a create/edit form's Hebrew year/month/day fields be *prefilled*
    as a convenience while someone's filling in the Gregorian date next to
    them. Login-required only, like HelpView - this is pure calendar
    math, not family data, so it doesn't need FamilyRequiredMixin.

    This is deliberately just a prefill, never authoritative - see
    AGENTS.md's "two calendars are recorded independently, never
    derived" rule. The conversion is ambiguous whenever the actual event
    happened after sunset (the Hebrew date has already advanced by then),
    which only whoever's entering the data can judge - the frontend
    (person_form.html/union_form.html via hebrew_autofill.js) only ever
    calls this when the Hebrew fields are still empty, so it can never
    clobber a value someone already typed in, and always leaves the
    result editable rather than locking it in.
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> JsonResponse:
        raw_date = request.POST.get("date")
        try:
            gregorian_date = dt.date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid or missing date."}, status=400)

        hebrew_date = gregorian_to_hebrew(gregorian_date)
        return JsonResponse(
            {
                "year": hebrew_date.year,
                "month": hebrew_date.month.value,
                "day": hebrew_date.day,
            }
        )


class HebrewToGregorianView(LoginRequiredMixin, View):
    """AJAX-only: converts a Hebrew date to its Gregorian equivalent.

    The read half of the Hebrew/Gregorian mismatch warning
    (hebrew_mismatch_warning.js) - GregorianToHebrewView above is the
    write half (prefilling empty Hebrew fields). This view never writes
    back into either date field; the JS only uses the returned date to
    compare against whatever Gregorian date is already entered, and warn
    if they're off by more than the one day sunset can plausibly explain
    - see that file's own docstring. Login-required only, same reasoning
    as GregorianToHebrewView: pure calendar math, not family data.
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> JsonResponse:
        # Both the HebrewDate construction and the actual conversion can
        # raise ValueError - constructing one only validates that
        # year/month/day are individually sensible (e.g. a real month
        # for that year), not that the resulting date is convertible to
        # a real Gregorian one at all. Found for real via Sentry: a
        # year far outside any plausible lifetime (a typo, or a still-
        # mid-edit value read too early - see hebrew_mismatch_warning.js)
        # constructs a perfectly valid HebrewDate, but hdate's own
        # to_gdate() then underflows Python's date.fromordinal and
        # raises "ordinal must be >= 1" - a real crash for what's
        # supposed to be a purely assistive, never-authoritative check
        # (see this view's own docstring).
        try:
            hebrew_date = HebrewDate(
                int(request.POST.get("year")),
                Months(int(request.POST.get("month"))),
                int(request.POST.get("day")),
            )
            gregorian_date = hebrew_to_gregorian(hebrew_date)
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid or missing Hebrew date."}, status=400)

        return JsonResponse({"date": gregorian_date.isoformat()})


def _occurrence_send_note(occurrence: Occurrence) -> str | None:
    """Explains why send_date differs from occurrence_date, or None when they match.

    Computed in Python, not the template - the weekday name has to go
    through Django's own date formatting (dateformat.format, via the
    "l" format char), never a raw Python date method, since
    family.apps.FamilyConfig.ready() patches Django's own weekday table
    to read "Shabbos" for Saturday (see AGENTS.md's "Dates" section) -
    only Django's formatting machinery reads that patched table.

    A Shabbos/Yom Tov shift is the real answer to "why isn't this the
    event date" once it applies - a lead time (notify_days_before) may
    have also been in play to get to the date being shifted from, but
    calling that out too just adds a second number that isn't why the
    date looks unusual.
    """
    if occurrence.send_date == occurrence.occurrence_date:
        return None
    send_date_label = dateformat.format(occurrence.send_date, "l, F j")
    if occurrence.shifted_for_shabbat_or_yomtov:
        reasons = " and ".join(occurrence.shift_reason_labels)
        return f"Sends earlier - {send_date_label} - since the date falls on {reasons}"
    notify_days_before = occurrence.event_type.notify_days_before
    if notify_days_before:
        day_word = "day" if notify_days_before == 1 else "days"
        return (
            f"Sends {send_date_label} - {occurrence.event_type.name} always sends "
            f"{notify_days_before} {day_word} ahead"
        )
    return None


class HomeView(FamilyRequiredMixin, View):
    """Landing page at `/` - the account's own person in their family tree, or Upcoming as a fallback.

    FamilyRequiredMixin's own dispatch() already guarantees request.family
    is resolved (real login, unambiguous family - redirecting to the
    switcher/onboarding otherwise) by the time get() runs, so
    request.self_person (tenants.middleware.CurrentFamilyMiddleware) is
    trustworthy here. accounts.views.VerifyMagicLinkView redirects here
    rather than resolving this itself, deliberately: right after that
    view's own login() call, request.family/request.self_person for
    that same request are still the *pre-login* values (middleware ran
    before the view, off of whatever request.user was at the start of
    the request) - redirecting here instead means the actual resolution
    happens on a fresh follow-up request, once the browser's sent the
    now-authenticated session back and middleware has run again for
    real, sidestepping that staleness rather than working around it.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        if request.self_person is not None:
            return redirect("family:family_tree", uuid=request.self_person.uuid)
        return redirect("family:dashboard")


class DashboardView(FamilyRequiredMixin, TemplateView):
    template_name = "family/dashboard.html"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        request = self.request

        # Everyone in the family is notified about everything by default,
        # so "upcoming" is just this family's occurrences, minus whatever
        # this account has muted. Pull a generous batch before trimming to
        # 20, since the mute check happens in Python per row.
        # Ordered explicitly by (occurrence_date, send_date, event_type
        # name) rather than relying on Occurrence.Meta's own default
        # ordering - occurrence_date, the real date the event falls on,
        # is what's actually displayed (see dashboard.html), so it has
        # to be the primary sort key too, not send_date. Wedding's own
        # notify_days_before=3 makes this a real, not just theoretical,
        # divergence: its send_date always lands ~3 days before its
        # occurrence_date, so sorting by send_date alone could show a
        # Wedding "3 days from now" ahead of a Birthday "tomorrow" in
        # the list, reading as out of chronological order even though
        # the dates shown are correct on their own. The third key keeps
        # multiple event types sharing a timeline point (below) in a
        # stable, predictable order instead of whatever incidental order
        # the DB happens to return them in.
        # send_date__gte=today OR is_sent=False, not send_date__gte alone -
        # matches send_due_notifications' own self-healing philosophy
        # (send_date can legitimately land in the past: a missed run, or a
        # same-day Shabbos/Yom Tov shift). An occurrence stuck unsent with a
        # past send_date is still real and about to send - it shouldn't
        # silently vanish from "Upcoming".
        candidates = list(
            Occurrence.objects.filter(models.Q(send_date__gte=timezone.localdate()) | models.Q(is_sent=False))
            .filter(
                models.Q(person__family=request.family)
                | models.Q(union__person_a__family=request.family)
                | models.Q(union__person_b__family=request.family)
            )
            .select_related("person", "union", "union__person_a", "union__person_b", "event_type")
            .order_by("occurrence_date", "send_date", "event_type__name")[:100]
        )

        # One preference/immediate-family batch load for this account across
        # every candidate below, instead of channels_for_account's own 2-4
        # queries repeated per candidate (up to 100 of them) - see
        # notifications.audience.PreferenceResolver and AGENTS.md's note on
        # this page's query cost. Scoped to whichever families actually show
        # up among the candidates already fetched above (a union can pull in
        # an in-law's other family), not just request.family.
        family_ids = set()
        for occurrence in candidates:
            if occurrence.person is not None:
                family_ids.add(occurrence.person.family_id)
            else:
                family_ids.add(occurrence.union.person_a.family_id)
                family_ids.add(occurrence.union.person_b.family_id)
        resolver = PreferenceResolver(account_ids=[request.user.id], family_ids=family_ids)

        upcoming = []
        for occurrence in candidates:
            if resolver.channels_for_account(
                request.user, occurrence.event_type, person=occurrence.person, union=occurrence.union
            ):
                upcoming.append(occurrence)
            if len(upcoming) >= 20:
                break

        # Grouped by (send_date, occurrence_date) - not occurrence_date
        # alone - for the timeline's one-point-per-date display: two
        # different people's occurrences can share an occurrence_date
        # without sharing a send_date (Wedding's own notify_days_before=3
        # vs. every other type's 0, for one), and grouping on
        # occurrence_date alone would lump a "coming up" Wedding in with
        # an "is today" Birthday landing on the very date it's about.
        # Safe to group adjacent-only (itertools.groupby, not a
        # sort+group) - the queryset above is already ordered by
        # (occurrence_date, send_date, ...), so rows sharing both are
        # already contiguous regardless of the order the two are named in
        # this key tuple.
        context["upcoming_groups"] = []
        for (send_date, occurrence_date), group in itertools.groupby(
            upcoming, key=lambda occurrence: (occurrence.send_date, occurrence.occurrence_date)
        ):
            occurrences = list(group)
            notes = [_occurrence_send_note(o) for o in occurrences]
            # The common case - one event type, or several that all
            # happen to share the same reason - gets a single line for
            # the whole group. Once two occurrences in the same group
            # disagree (e.g. one shifted for Yom Tov, another just on
            # its own type's usual lead time), a shared line would state
            # one of those reasons as if it applied to both - fall back
            # to a note per occurrence instead, only when that's
            # actually necessary.
            if len(set(notes)) == 1:
                group_send_note = notes[0]
            else:
                group_send_note = None
                for occurrence, note in zip(occurrences, notes, strict=True):
                    occurrence.send_note = note
            context["upcoming_groups"].append(
                {
                    "send_date": send_date,
                    "occurrence_date": occurrence_date,
                    "occurrences": occurrences,
                    "send_note": group_send_note,
                }
            )
        return context


class PersonListView(FamilyRequiredMixin, ListView):
    """Lists this family's people, hiding lineage-only stubs by default.

    Lineage-only stubs (notifications_enabled=False - see
    Person.notifications_enabled) are hidden by default: they're
    ancestors recorded for the tree, not people anyone's looking someone
    up by name to find. An editor/owner can still reveal them here via
    ?show_untracked=1 - everyone else can still reach one through the
    tree or a tracked relation's profile, neither of which filters on
    this.
    """

    model = Person
    template_name = "family/person_list.html"
    context_object_name = "people"

    def _can_show_untracked(self) -> bool:
        return self.request.family_role in FamilyMembership.EDITOR_ROLES

    def get_queryset(self) -> QuerySet[Person]:
        people = Person.objects.filter(family=self.request.family)
        if not (self._can_show_untracked() and self.request.GET.get("show_untracked")):
            people = people.filter(notifications_enabled=True)
        query = self.request.GET.get("q", "").strip()
        if query:
            people = people.filter(
                models.Q(first_name_en__icontains=query)
                | models.Q(last_name_en__icontains=query)
                | models.Q(nickname__icontains=query)
                | models.Q(first_name_he__icontains=query)
                | models.Q(last_name_he__icontains=query)
            )
        return people

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "").strip()
        context["can_show_untracked"] = self._can_show_untracked()
        context["show_untracked"] = self._can_show_untracked() and bool(
            self.request.GET.get("show_untracked")
        )
        return context


class PersonDetailView(FamilyRequiredMixin, DetailView):
    model = Person
    template_name = "family/person_detail.html"
    context_object_name = "person"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    def get_object(self, queryset: QuerySet[Person] | None = None) -> Person:
        person = super().get_object(queryset)
        if not person_is_visible(person, self.request.family):
            raise Http404
        return person

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        request = self.request
        person = self.object

        context["can_see_birth_year"] = can_see_birth_year(
            person,
            can_edit=request.family_permissions.can_edit,
            viewer_account_id=request.user.id,
        )

        children = (
            Person.objects.filter(models.Q(father=person) | models.Q(mother=person))
            .distinct()
            .order_by("dob_gregorian")
        )
        unions = list(
            Union.objects.filter(models.Q(person_a=person) | models.Q(person_b=person)).select_related(
                "person_a", "person_b"
            )
        )
        for union in unions:
            union.other_person = union.other(person)

        # Same "+ Add father/mother/child" placeholders as the family
        # tree (see family_tree.html's own goAddRelative()), landing on
        # the same PersonCreateView query-param contract - link_as/
        # link_of for father/mother (the new person doesn't own the
        # relationship), a plain father/mother param for a child (a real
        # field on the person being created). Built server-side here,
        # not in JS, since this page already has person/unions as real
        # objects with no anchor-lookup step needed the way the tree's
        # flat JSON does. Gated the same way "+ Add spouse" already is -
        # only for someone in the viewer's own editable family.
        context["can_add_relatives"] = (
            person.family_id == request.family.id and request.family_permissions.can_edit
        )
        if context["can_add_relatives"]:
            next_param = urlencode({"next": request.path})
            if not person.father:
                context["add_father_url"] = (
                    f"{reverse('family:person_create')}?link_as=father&link_of={person.uuid}&{next_param}"
                )
            if not person.mother:
                context["add_mother_url"] = (
                    f"{reverse('family:person_create')}?link_as=mother&link_of={person.uuid}&{next_param}"
                )
            # The other parent is prefilled too, same as the tree does,
            # when this person has a recorded spouse - whichever union
            # comes first, matching the tree's own "any spouse found"
            # simplification rather than picking a specific one.
            child_parent_field = "mother" if person.gender == Person.Gender.FEMALE else "father"
            add_child_params = {child_parent_field: str(person.uuid), "next": request.path}
            if unions:
                spouse = unions[0].other_person
                spouse_field = "mother" if spouse.gender == Person.Gender.FEMALE else "father"
                if spouse_field != child_parent_field:
                    add_child_params[spouse_field] = str(spouse.uuid)
            context["add_child_url"] = f"{reverse('family:person_create')}?{urlencode(add_child_params)}"

        my_channels = available_channels(request.user)

        available_event_types = EventType.objects.filter(
            models.Q(family__isnull=True) | models.Q(family=request.family)
        )

        # Everyone starts notified about everything - a channel shows up
        # here at all once it has a usable destination and isn't switched
        # off globally; "subscribed" reflects whether it's muted, not
        # whether anyone opted in.
        # An untracked person (notifications_enabled=False - a lineage-
        # only stub, see AGENTS.md) never gets most events computed for
        # them at all (notifications.tasks._subject_pairs), so offering
        # most toggles here would just be dead controls - except an
        # event_type.always_schedule type (Yahrzeit), which schedules
        # regardless, so its toggle is real even for an untracked person
        # (a real ancestor entered only as a lineage stub). This is
        # checked per event type, not once for the whole card, for
        # exactly that reason - see the "Notify me" card's own
        # untracked-specific message in person_detail.html for what an
        # owner/editor sees for the toggles that *are* still hidden.
        event_rows = []
        for event_type in available_event_types.filter(applies_to_union=False):
            if not event_type.always_schedule and not person.notifications_enabled:
                continue
            # Broadcast has no per-person override - see
            # NotificationPreference.clean() and AGENTS.md - so it
            # never gets a toggle here, only the whole-type mute on
            # My Notifications (notifications.views.SubscriptionsView).
            if event_type.code == EventType.BuiltinCode.BROADCAST:
                continue
            if event_type.anchor == EventType.Anchor.DEATH and person.is_living:
                continue
            # Symmetric to the DEATH-anchor check above: once someone has
            # died there's no more birthday (or bar/bat mitzvah) to
            # celebrate, only the yahrzeit - see notifications.tasks for
            # the matching check in the actual scheduling logic.
            if event_type.anchor == EventType.Anchor.BIRTH and not person.is_living:
                continue
            # These only ever apply to one gender, and stop being relevant
            # once that birthday has already passed - no point offering a
            # bar mitzvah toggle on a woman's page, or a 40-year-old's.
            if event_type.code == EventType.BuiltinCode.BAR_MITZVAH:
                if person.gender != Person.Gender.MALE:
                    continue
                if person_has_passed_coming_of_age(person):
                    continue
            if event_type.code == EventType.BuiltinCode.BAT_MITZVAH:
                if person.gender != Person.Gender.FEMALE:
                    continue
                if person_has_passed_coming_of_age(person):
                    continue
            channels = channel_rows(request.user, event_type, my_channels, person=person)
            event_rows.append({"event_type": event_type, "channels": channels})

        union_event_types = list(available_event_types.filter(applies_to_union=True))
        union_rows = []
        for union in unions:
            # Covers "not married", "either spouse has died" (no stored
            # "widowed" flip required - same reasoning as Union.is_upcoming
            # for engagements), and "either spouse is untracked" - none of
            # these ever get an Occurrence computed (notifications.tasks),
            # so a toggle here would be a dead control either way.
            if not union_is_eligible_for_notifications(union):
                continue
            for event_type in union_event_types:
                # Wedding is only relevant before the wedding itself has
                # happened; Anniversary is the reverse - there's nothing
                # to celebrate the anniversary of yet. Same "stop/start
                # being relevant" pattern as the bar/bat mitzvah gating
                # above, just keyed on the wedding date instead of age.
                # Engagement follows Wedding's own gating (only relevant
                # pre-wedding), but Engagement Anniversary is deliberately
                # not gated on is_upcoming at all - unlike Wedding's own
                # Anniversary, it's meant to keep recurring indefinitely
                # alongside the real anniversary once married, not stop
                # and get replaced by it.
                if event_type.code == EventType.BuiltinCode.WEDDING and not union.is_upcoming:
                    continue
                if event_type.code == EventType.BuiltinCode.ANNIVERSARY and union.is_upcoming:
                    continue
                if event_type.code == EventType.BuiltinCode.ENGAGEMENT and not union.is_upcoming:
                    continue
                channels = channel_rows(request.user, event_type, my_channels, union=union)
                union_rows.append({"union": union, "event_type": event_type, "channels": channels})

        has_any_channel = bool(my_channels)

        context.update(
            {
                "children": children,
                "unions": unions,
                "event_rows": event_rows,
                "union_rows": union_rows,
                "has_any_channel": has_any_channel,
            }
        )
        if request.family_role in FamilyMembership.EDITOR_ROLES:
            context["history_events"] = person_history(person, unions=unions)
        return context


class FamilyTreeView(FamilyRequiredMixin, DetailView):
    """Renders the full family tree via the family-chart JS library.

    Every ancestor and descendant generation is included, not just
    grandparents/grandchildren, via the family-chart JS library instead
    of a hand-rolled CSS org-chart - see family/tree_chart.py for how the
    data gets built and templates/family/family_tree.html for the
    library setup.
    """

    model = Person
    template_name = "family/family_tree.html"
    context_object_name = "person"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"

    def get_object(self, queryset: QuerySet[Person] | None = None) -> Person:
        person = super().get_object(queryset)
        if not person_is_visible(person, self.request.family):
            raise Http404
        return person

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        people = visible_people_queryset(self.request.family).select_related("father", "mother")
        context["chart_data"] = build_chart_data(
            people, main_person=self.object, editable_family_id=self.request.family.id
        )
        context["can_edit_tree"] = self.request.family_role in FamilyMembership.EDITOR_ROLES
        return context


class PersonCreateView(FamilyEditorRequiredMixin, CreateView):
    """Creates a person - also the landing point for the tree's "+ Add" placeholders.

    The family tree's "+ Add father/mother/child" placeholders (see
    family_tree.html) land here too: `father`/`mother` in the query
    string prefill those fields directly since they're real fields on the
    person being created (the "add a child" case), while
    `link_as`+`link_of` handle the reverse direction (the "add a
    father/mother" case, where the *new* person isn't the one that owns
    the relationship) by linking the existing anchor person's
    father/mother field to the newly created person after saving.
    """

    model = Person
    form_class = PersonForm
    template_name = "family/person_form.html"

    def _link_as(self) -> str | None:
        link_as = self.request.POST.get("link_as") or self.request.GET.get("link_as")
        return link_as if link_as in ("father", "mother") else None

    def _link_of(self) -> Person | None:
        link_of = self.request.POST.get("link_of") or self.request.GET.get("link_of")
        if not link_of:
            return None
        return get_object_or_404(Person, uuid=link_of, family=self.request.family)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["family"] = self.request.family
        return kwargs

    def get_initial(self) -> dict[str, Any]:
        initial = super().get_initial()
        for field in ("father", "mother"):
            person_uuid = self.request.GET.get(field)
            if person_uuid:
                initial[field] = get_object_or_404(Person, uuid=person_uuid, family=self.request.family).pk

        link_as = self._link_as()
        if link_as == "father":
            initial["gender"] = Person.Gender.MALE
        elif link_as == "mother":
            initial["gender"] = Person.Gender.FEMALE
        return initial

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["link_as"] = self._link_as()
        context["link_of"] = self._link_of()
        context["next_url"] = self.request.GET.get("next", "")
        return context

    def form_valid(self, form: PersonForm) -> HttpResponse:
        # Occurrence recompute (this new person's, and the anchor's below)
        # happens via family.signals._recompute_person_occurrences,
        # triggered by each object's own save().
        response = super().form_valid(form)

        link_as = self._link_as()
        anchor = self._link_of()
        if link_as and anchor is not None:
            setattr(anchor, link_as, self.object)
            # A plain .save() never calls clean() - unlike PersonForm's
            # own save() (a ModelForm always runs full_clean() first) -
            # so this is the one write path that could otherwise silently
            # create the exact kind of cycle Person.clean() now rejects
            # (see AGENTS.md/a real incident: a newly-added parent whose
            # own father/mother field was mistakenly set back to the
            # person they were just added as the parent of). Validate
            # explicitly rather than let a bad link through unnoticed.
            try:
                anchor.full_clean()
            except ValidationError as exc:
                messages.error(
                    self.request,
                    f"Added {form.instance.display_name}, but couldn't set them as "
                    f"{anchor.display_name}'s {link_as}: {' '.join(exc.messages)}",
                )
            else:
                anchor.save(update_fields=[link_as])
                messages.success(
                    self.request, f"Added {form.instance.display_name} as {anchor.display_name}'s {link_as}."
                )
        else:
            messages.success(self.request, f"Added {form.instance.display_name}.")
        return response

    def get_success_url(self) -> str:
        return self.request.POST.get("next") or reverse("family:person_detail", args=[self.object.uuid])


class PersonUpdateView(FamilyScopedMixin, FamilyEditorRequiredMixin, UpdateView):
    """Editing is narrower than viewing: only this family's own records.

    Not an in-law visible only through a Union - see FamilyScopedMixin.
    """

    model = Person
    form_class = PersonForm
    template_name = "family/person_form.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    family_lookup = "family"

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["family"] = self.request.family
        return kwargs

    def form_valid(self, form: PersonForm) -> HttpResponse:
        # Occurrence recompute (this person's own, and their unions' - a
        # death date recorded here affects Anniversary eligibility too)
        # happens via family.signals._recompute_person_occurrences,
        # triggered by the save() inside super().form_valid().
        response = super().form_valid(form)
        messages.success(self.request, f"Saved changes to {form.instance.display_name}.")
        return response

    def get_success_url(self) -> str:
        return reverse("family:person_detail", args=[self.object.uuid])


class PersonDeleteView(FamilyScopedMixin, FamilyOwnerRequiredMixin, DeleteView):
    model = Person
    template_name = "family/person_confirm_delete.html"
    success_url = reverse_lazy("family:person_list")
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    family_lookup = "family"

    def form_valid(self, form: forms.Form) -> HttpResponse:
        messages.success(self.request, f"Removed {self.object.display_name} from the family.")
        return super().form_valid(form)


class UnionCreateView(FamilyEditorRequiredMixin, CreateView):
    model = Union
    form_class = UnionForm
    template_name = "family/union_form.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # The anchor person must be in your own family - you can't
        # unilaterally add a marriage onto someone else's ledger entry.
        self.person_a = get_object_or_404(Person, uuid=kwargs["person_uuid"], family=request.family)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["person_a"] = self.person_a
        kwargs["family"] = self.request.family
        return kwargs

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["person_a"] = self.person_a
        existing_unions = list(
            Union.objects.filter(
                models.Q(person_a=self.person_a) | models.Q(person_b=self.person_a)
            ).select_related("person_a", "person_b")
        )
        for union in existing_unions:
            union.other_person = union.other(self.person_a)
        context["existing_unions"] = existing_unions
        context["union_status_choices"] = Union.Status.choices
        context["next_url"] = self.request.GET.get("next", "")
        return context

    def form_valid(self, form: UnionForm) -> HttpResponse:
        # Occurrence recompute happens via
        # family.signals._recompute_union_occurrences, triggered by the
        # save() inside super().form_valid() and inside
        # _apply_existing_union_status_updates()'s own union.save().
        response = super().form_valid(form)
        self._apply_existing_union_status_updates()
        messages.success(
            self.request,
            f"Added {form.instance.person_b.display_name} as {self.person_a.display_name}'s spouse.",
        )
        return response

    def _apply_existing_union_status_updates(self) -> None:
        # Adding a new spouse is exactly the moment a prior marriage's
        # status needs updating (divorced, widowed) - offer it inline
        # instead of making that a separate trip to the edit page.
        valid_statuses = {choice for choice, _ in Union.Status.choices}
        existing = Union.objects.filter(
            models.Q(person_a=self.person_a) | models.Q(person_b=self.person_a)
        ).exclude(pk=self.object.pk)
        for union in existing:
            new_status = self.request.POST.get(f"existing_union_status_{union.uuid}")
            if new_status in valid_statuses and new_status != union.status:
                union.status = new_status
                union.save(update_fields=["status"])

    def get_success_url(self) -> str:
        return self.request.POST.get("next") or reverse("family:person_detail", args=[self.person_a.uuid])


class UnionUpdateView(FamilyScopedMixin, FamilyEditorRequiredMixin, UpdateView):
    """Either side's family can keep a shared marriage record accurate.

    See FamilyScopedMixin's family_lookup list.
    """

    model = Union
    form_class = UnionEditForm
    template_name = "family/union_form.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    family_lookup = ["person_a__family", "person_b__family"]

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["person_a"] = self.object.person_a
        return context

    def form_valid(self, form: UnionEditForm) -> HttpResponse:
        # Occurrence recompute happens via
        # family.signals._recompute_union_occurrences, triggered by the
        # save() inside super().form_valid().
        response = super().form_valid(form)
        messages.success(self.request, "Saved changes to the marriage.")
        return response

    def get_success_url(self) -> str:
        return reverse("family:person_detail", args=[self.object.person_a.uuid])


class UnionDeleteView(FamilyScopedMixin, FamilyOwnerRequiredMixin, DeleteView):
    model = Union
    template_name = "family/union_confirm_delete.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    family_lookup = ["person_a__family", "person_b__family"]

    def get_success_url(self) -> str:
        return reverse("family:person_detail", args=[self.object.person_a.uuid])

    def form_valid(self, form: forms.Form) -> HttpResponse:
        messages.success(self.request, "Removed the marriage record.")
        return super().form_valid(form)
