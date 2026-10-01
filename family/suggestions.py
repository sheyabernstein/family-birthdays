"""Member-suggested Person/Union additions/edits, pending owner/editor review.

See family.models.Suggestion's own docstring for the data model. Split
out of views.py (already large) since this is a self-contained concern -
except for SuggestionApplyMixin, which is mixed directly into the real
Person/Union Create/UpdateViews in views.py (see that class's own
docstring for why review deliberately goes through the real forms
rather than a parallel apply path).
"""

import datetime
from typing import Any

from django.contrib import messages
from django.core.signals import request_finished
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, TemplateView
from reversion.signals import post_revision_commit

from family.access import person_is_visible, person_is_visible_to, union_is_visible
from family.forms import PersonSuggestionForm, UnionSuggestionForm
from family.models import SUGGESTABLE_PERSON_FIELDS, SUGGESTABLE_UNION_FIELDS, Person, Suggestion, Union
from tenants.mixins import FamilyEditorRequiredMixin, FamilyRequiredMixin


def _value_for_json(value: Any) -> Any:
    """Converts one cleaned_data value into a JSON-safe primitive for Suggestion.proposed_changes.

    A date becomes its isoformat() string (Django's own DateField accepts
    that same string back as initial= data); a Person (from a
    ModelChoiceField - father/mother/link_with) becomes its pk.
    Everything else (str/int/bool/None) is already JSON-safe.
    """
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, Person):
        return value.pk
    return value


def _serialize_changes(cleaned_data: dict[str, Any], field_names: list[str]) -> dict[str, Any]:
    return {name: _value_for_json(cleaned_data[name]) for name in field_names if name in cleaned_data}


def _check_visible(request: HttpRequest, person: Person) -> None:
    """Raises Http404 for a person this submitter can't currently see - never a different error.

    Same "don't even reveal whether an invisible record exists" shape as
    PersonDetailView.get_object - a 403 here would itself leak that
    *something* is there, just gated off.
    """
    if not person_is_visible(person, request.family):
        raise Http404
    if not person_is_visible_to(
        person, viewer=request.self_person, can_edit=request.family_permissions.can_edit
    ):
        raise Http404


class PersonSuggestionCreateView(FamilyRequiredMixin, FormView):
    """Suggests a new Person, or an edit to an existing one visible to the submitter.

    Not a CreateView - the bound form's own instance (a possibly-unsaved
    Person) is only ever used for validation here, never saved; a
    Suggestion row is what actually gets created. See form_valid.
    """

    form_class = PersonSuggestionForm
    template_name = "family/suggestion_person_form.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.target_person = None
        person_uuid = request.GET.get("person") or request.POST.get("person")
        if person_uuid:
            self.target_person = get_object_or_404(Person, uuid=person_uuid)
            _check_visible(request, self.target_person)

        # Two different prefill shapes, both only meaningful for a
        # suggest-add (same as the sections/fields themselves):
        # ?link_kind=&link_with= ("suggest a new spouse/father/mother for
        # X" on person_detail.html) prefills the relationship section;
        # ?father=/?mother= ("suggest a child of X") prefills that field
        # directly on the new person's own form, same query-param names
        # PersonCreateView already uses for editors' own "+Add child".
        self.link_kind = None
        self.link_with = None
        self.prefill_parent = {}
        if not self.target_person:
            link_kind = request.GET.get("link_kind") or request.POST.get("link_kind")
            link_with_uuid = request.GET.get("link_with") or request.POST.get("link_with")
            if link_kind in Suggestion.LinkKind.values and link_with_uuid:
                self.link_with = get_object_or_404(Person, uuid=link_with_uuid)
                _check_visible(request, self.link_with)
                self.link_kind = link_kind
            for field in ("father", "mother"):
                parent_uuid = request.GET.get(field)
                if parent_uuid:
                    # Same-family only, same as PersonForm's own father/
                    # mother fields - see PersonSuggestionForm.__init__.
                    parent = get_object_or_404(Person, uuid=parent_uuid, family=request.family)
                    _check_visible(request, parent)
                    self.prefill_parent[field] = parent
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.target_person or Person()
        # An edit uses the target's own family (an in-law's own father/
        # mother picker is scoped to *their* family, not the submitter's -
        # see PersonSuggestionForm.__init__); an add belongs to the
        # submitter's own family, same as PersonCreateView.
        kwargs["family"] = self.target_person.family if self.target_person else self.request.family
        kwargs["viewer"] = self.request.self_person
        kwargs["can_edit"] = self.request.family_permissions.can_edit
        return kwargs

    def get_initial(self) -> dict[str, Any]:
        initial = super().get_initial()
        if self.link_kind:
            initial["link_kind"] = self.link_kind
            initial["link_with"] = self.link_with.pk
        for field, parent in self.prefill_parent.items():
            initial[field] = parent.pk
        return initial

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["target_person"] = self.target_person
        return context

    def form_valid(self, form: PersonSuggestionForm) -> HttpResponse:
        suggestion = Suggestion(
            family=self.request.family,
            submitted_by=self.request.user,
            target_model=Suggestion.TargetModel.PERSON,
            target_person=self.target_person,
            proposed_changes=_serialize_changes(form.cleaned_data, SUGGESTABLE_PERSON_FIELDS),
        )
        link_kind = form.cleaned_data.get("link_kind")
        if link_kind:
            suggestion.link_kind = link_kind
            suggestion.link_with = form.cleaned_data["link_with"]
            link_union_changes = form.link_union_changes()
            if link_union_changes is not None:
                suggestion.link_union_changes = _serialize_changes(
                    link_union_changes, SUGGESTABLE_UNION_FIELDS
                )
        suggestion.save()
        messages.success(self.request, "Thanks - your suggestion has been submitted for review.")
        return redirect("family:suggestions")


class UnionSuggestionCreateView(FamilyRequiredMixin, FormView):
    """Suggests a new union between two existing people, or an edit to an existing union.

    Reached either with ?anchor=<person uuid> (suggest a new union from
    that person's own page) or ?union=<union uuid> (suggest an edit to
    an existing one) - mutually exclusive, mirroring PersonCreateView's
    own link_as/link_of vs plain father/mother query-param contract.
    """

    form_class = UnionSuggestionForm
    template_name = "family/suggestion_union_form.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        self.target_union = None
        union_uuid = request.GET.get("union") or request.POST.get("union")
        anchor_uuid = request.GET.get("anchor") or request.POST.get("anchor")
        if union_uuid:
            self.target_union = get_object_or_404(Union, uuid=union_uuid)
            if not union_is_visible(self.target_union, request.family):
                raise Http404
            _check_visible(request, self.target_union.person_a)
            _check_visible(request, self.target_union.person_b)
            self.person_a = self.target_union.person_a
        elif anchor_uuid:
            self.person_a = get_object_or_404(Person, uuid=anchor_uuid)
            _check_visible(request, self.person_a)
        else:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.target_union or Union()
        kwargs["person_a"] = self.person_a
        kwargs["family"] = self.request.family
        kwargs["viewer"] = self.request.self_person
        kwargs["can_edit"] = self.request.family_permissions.can_edit
        return kwargs

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["person_a"] = self.person_a
        context["target_union"] = self.target_union
        return context

    def form_valid(self, form: UnionSuggestionForm) -> HttpResponse:

        suggestion = Suggestion(
            family=self.request.family,
            submitted_by=self.request.user,
            target_model=Suggestion.TargetModel.UNION,
            target_union=self.target_union,
            proposed_changes=_serialize_changes(form.cleaned_data, SUGGESTABLE_UNION_FIELDS),
        )
        if not self.target_union:
            suggestion.proposed_person_a = self.person_a
            suggestion.proposed_person_b = form.cleaned_data["person_b"]
        suggestion.save()
        messages.success(self.request, "Thanks - your suggestion has been submitted for review.")
        return redirect("family:suggestions")


class SuggestionsView(FamilyRequiredMixin, TemplateView):
    """One page: "My Suggestions" for everyone, plus "Needs Your Review" for owners/editors.

    Was two separate pages/views - merged since they're really one
    audience's two different relationships to the same underlying list,
    same shape as a PR dashboard's "created by you"/"review requested."
    """

    template_name = "family/suggestions.html"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["my_suggestions"] = Suggestion.objects.filter(
            family=self.request.family, submitted_by=self.request.user
        ).select_related(
            "target_person",
            "target_union__person_a",
            "target_union__person_b",
            "proposed_person_a",
            "proposed_person_b",
            "link_with",
            "resulting_person",
            "reviewed_by",
        )
        if self.request.family_permissions.can_edit:
            queue = list(
                Suggestion.objects.filter(
                    family=self.request.family, status=Suggestion.Status.PENDING
                ).select_related(
                    "submitted_by",
                    "target_person",
                    "target_union__person_a",
                    "target_union__person_b",
                    "proposed_person_a",
                    "proposed_person_b",
                    "link_with",
                    "resulting_person",
                )
            )
            for suggestion in queue:
                suggestion.apply_url, suggestion.apply_label = _apply_url_and_label(suggestion)
            context["review_queue"] = queue
        return context


def _apply_url_and_label(suggestion: Suggestion) -> tuple[str, str]:
    """Where the review queue's "Approve"/"Continue" link sends a reviewer, and what to call it.

    Always a real PersonCreateView/PersonUpdateView/UnionCreateView/
    UnionUpdateView URL with ?suggestion=<uuid> - SuggestionApplyMixin
    (mixed into all four) does the actual prefill/close-out. Never an
    apply-here action of its own - see that mixin's own docstring for
    why review goes through the real forms instead.
    """
    qs = f"?suggestion={suggestion.uuid}"
    if suggestion.target_model == Suggestion.TargetModel.PERSON:
        if suggestion.link_kind and suggestion.resulting_person_id:
            next_view = (
                "family:union_create"
                if suggestion.link_kind == Suggestion.LinkKind.SPOUSE
                else "family:person_update"
            )
            return f"{reverse(next_view, args=[suggestion.link_with.uuid])}{qs}", "Continue"
        if suggestion.target_person_id:
            return (
                f"{reverse('family:person_update', args=[suggestion.target_person.uuid])}{qs}",
                "Review & apply",
            )
        return f"{reverse('family:person_create')}{qs}", "Review & apply"
    if suggestion.target_union_id:
        return f"{reverse('family:union_update', args=[suggestion.target_union.uuid])}{qs}", "Review & apply"
    return f"{reverse('family:union_create', args=[suggestion.proposed_person_a.uuid])}{qs}", "Review & apply"


class SuggestionRejectView(FamilyEditorRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        # resulting_person_id__isnull=True excludes a suggestion whose
        # step 1 already created a real Person - rejecting it here would
        # only delete the Suggestion row (status=REJECTED, actually - but
        # see the comment on the equivalent guard below), silently
        # orphaning that Person with no trace of where it came from. The
        # template already hides the Reject control in that state; this
        # is the server-side half of the same rule - see SuggestionsView's
        # template for the "Continue"-only affordance a reviewer gets
        # instead once a suggestion reaches that state.
        claimed = Suggestion.objects.filter(
            uuid=uuid,
            family=request.family,
            status=Suggestion.Status.PENDING,
            resulting_person_id__isnull=True,
        ).update(
            status=Suggestion.Status.REJECTED,
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
            reviewer_note=request.POST.get("reviewer_note", "").strip(),
        )
        if claimed:
            messages.success(request, "Suggestion rejected.")
        else:
            messages.error(request, "This suggestion was already reviewed.")
        return redirect("family:suggestions")


class SuggestionWithdrawView(FamilyRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, uuid: str) -> HttpResponse:
        # resulting_person_id__isnull=True - see the matching comment on
        # SuggestionRejectView.post. Deleting the Suggestion row here
        # would be worse than rejecting it: resulting_person is SET_NULL,
        # so the already-created Person would be left in the ledger with
        # no surviving record at all of where it came from.
        deleted, _ = Suggestion.objects.filter(
            uuid=uuid,
            family=request.family,
            submitted_by=request.user,
            status=Suggestion.Status.PENDING,
            resulting_person_id__isnull=True,
        ).delete()
        if deleted:
            messages.success(request, "Suggestion withdrawn.")
        else:
            messages.error(request, "That suggestion can't be withdrawn.")
        return redirect("family:suggestions")


class SuggestionApplyMixin:
    """Lets a Person/Union Create/UpdateView optionally prefill from, and close out, a Suggestion.

    Entirely opt-in via ?suggestion=<uuid> - absent that param, these
    views behave exactly as they always have. The reviewer sees the
    real PersonForm/UnionForm, prefilled from the suggestion's own
    proposed data, and can change anything before saving - the save
    itself is a completely ordinary form_valid(), so reversion/signals
    behave exactly as a direct edit would. revision.user stays the
    reviewer (normal RevisionMiddleware behavior) - attribution to the
    original submitter lives on the Suggestion itself
    (applied_revision/link_applied_revision), read by
    family.history to annotate the History card, never by overriding
    reversion.set_user().

    Drives the two-step combined-suggestion flow: a Person-add
    suggestion that also proposed a relationship to an existing person
    (link_kind/link_with) doesn't close out after the person is saved -
    it stores resulting_person and redirects the reviewer straight into
    the right second step (UnionCreateView for link_kind=SPOUSE,
    PersonUpdateView for FATHER/MOTHER), with the just-created person
    already pickable/assignable there. Only that second save actually
    marks the suggestion APPROVED. Abandoning after the first step
    leaves a resumable (not duplicable) state - see SuggestionsView's
    template, which offers "Continue" instead of "Approve" once
    resulting_person is set.
    """

    suggestion_target_model: str = ""
    # True for PersonCreateView/UnionCreateView (this view's own save can
    # be either a plain suggest-add, or one leg of a two-step link);
    # False (the default) for PersonUpdateView/UnionUpdateView, which
    # only ever edit a specific existing row - either the suggestion's
    # own target, or (PersonUpdateView only) the link_with anchor of a
    # father/mother link's second step.
    suggestion_creates_new: bool = False

    def _suggestion_uuid(self) -> str | None:
        return self.request.GET.get("suggestion") or self.request.POST.get("suggestion")

    def get_suggestion(self) -> Suggestion | None:
        if hasattr(self, "_suggestion_cache"):
            return self._suggestion_cache
        uuid = self._suggestion_uuid()
        if not uuid:
            self._suggestion_cache = None
            return None
        suggestion = get_object_or_404(
            Suggestion, uuid=uuid, family=self.request.family, status=Suggestion.Status.PENDING
        )
        if not self._suggestion_matches(suggestion):
            raise Http404
        self._suggestion_cache = suggestion
        return suggestion

    def _suggestion_matches(self, suggestion: Suggestion) -> bool:
        """Whether this specific view visit is a legitimate step for `suggestion` - see class docstring."""
        if self.suggestion_target_model == Suggestion.TargetModel.PERSON:
            if self.suggestion_creates_new:
                # PersonCreateView: a still-pending add, not yet at or past its own step 1.
                return (
                    suggestion.target_model == Suggestion.TargetModel.PERSON
                    and suggestion.target_person_id is None
                    and suggestion.resulting_person_id is None
                )
            # PersonUpdateView: a plain suggested edit of *this* person,
            # or step 2 of a father/mother link onto *this* person.
            is_plain_edit = suggestion.target_person_id == self.object.pk
            is_link_step = (
                suggestion.link_kind in (Suggestion.LinkKind.FATHER, Suggestion.LinkKind.MOTHER)
                and suggestion.link_with_id == self.object.pk
                and suggestion.resulting_person_id is not None
            )
            return is_plain_edit or is_link_step

        if self.suggestion_creates_new:
            # UnionCreateView: a plain union-add anchored on this page's
            # own person_a, or step 2 of a spouse link whose person-leg
            # is already done.
            is_plain_add = (
                suggestion.target_model == Suggestion.TargetModel.UNION
                and suggestion.target_union_id is None
                and suggestion.proposed_person_a_id == self.person_a.pk
            )
            is_link_step = (
                suggestion.target_model == Suggestion.TargetModel.PERSON
                and suggestion.link_kind == Suggestion.LinkKind.SPOUSE
                and suggestion.link_with_id == self.person_a.pk
                and suggestion.resulting_person_id is not None
            )
            return is_plain_add or is_link_step
        # UnionUpdateView: a plain suggested edit of *this* union - links never land here.
        return suggestion.target_union_id == self.object.pk

    def get_initial(self) -> dict[str, Any]:
        initial = super().get_initial()
        suggestion = self.get_suggestion()
        if suggestion is None:
            return initial
        if self.suggestion_target_model == Suggestion.TargetModel.PERSON:
            if suggestion.link_with_id and suggestion.resulting_person_id:
                # Step 2 of a father/mother link - this person is being
                # assigned the just-created one as their father/mother.
                initial[suggestion.link_kind] = suggestion.resulting_person_id
            else:
                initial.update(suggestion.proposed_changes)
        elif suggestion.target_model == Suggestion.TargetModel.PERSON:
            # Step 2 of a spouse link - the union's own fields live in
            # link_union_changes (proposed_changes was the person's).
            initial.update(suggestion.link_union_changes or {})
            initial["existing_spouse"] = suggestion.resulting_person_id
        else:
            initial.update(suggestion.proposed_changes)
            initial["existing_spouse"] = suggestion.proposed_person_b_id
        return initial

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["applying_suggestion"] = self.get_suggestion()
        return context

    def _link_applied_revision(self, suggestion: "Suggestion", target_object: Any, field: str) -> None:
        """Fills in applied_revision/link_applied_revision once the real save actually commits.

        RevisionMiddleware wraps the *entire* request in one outer
        revision context that only writes its Revision/Version rows when
        IT exits - strictly after this view's form_valid() has already
        returned (reversion's own "only the last/outermost open frame for
        a db actually saves" rule - a nested create_revision() here
        doesn't change that, it just merges its state up into the still-
        open outer frame instead of saving early). So there's no revision
        to read back synchronously here - this connects to
        post_revision_commit, reversion's own signal for "a revision was
        just actually written," and does the linking whenever that fires
        for *this* object, then disconnects itself.

        post_revision_commit only fires when the outer revision context's
        own _save_revision() actually runs - which it skips entirely if
        the request's DB transaction ends up marked for rollback instead
        of committing (an unrelated exception later in the middleware
        chain, say). Without a backstop, _on_commit would then never
        disconnect: it'd stay registered on this process-global signal
        for the life of the worker, holding this closure, and run its
        (harmless but wasted) equality check against every future
        revision commit anywhere in the app. request_finished fires at
        the end of every request regardless of how the DB transaction
        resolved, so connecting a matching one-shot cleanup to it closes
        that gap.
        """

        def _on_commit(sender: Any, revision: Any, versions: Any, **kwargs: Any) -> None:
            for version in versions:
                if version.object_id == str(target_object.pk) and version.content_type.model_class() is type(
                    target_object
                ):
                    Suggestion.objects.filter(pk=suggestion.pk).update(**{field: revision.pk})
                    break
            post_revision_commit.disconnect(_on_commit)
            request_finished.disconnect(_cleanup)

        def _cleanup(sender: Any, **kwargs: Any) -> None:
            post_revision_commit.disconnect(_on_commit)
            request_finished.disconnect(_cleanup)

        post_revision_commit.connect(_on_commit, weak=False)
        request_finished.connect(_cleanup, weak=False)

        post_revision_commit.connect(_on_commit, weak=False)

    def form_valid(self, form: Any) -> HttpResponse:
        suggestion = self.get_suggestion()
        if suggestion is None:
            return super().form_valid(form)

        is_step_one = (
            self.suggestion_creates_new
            and self.suggestion_target_model == Suggestion.TargetModel.PERSON
            and suggestion.target_person_id is None
            and suggestion.link_kind
            and suggestion.resulting_person_id is None
        )

        # The actual Person/Union save and the claim below share one
        # atomic block so a lost race rolls back the save too - without
        # this, two reviewers (or two tabs) opening the same suggestion's
        # apply URL concurrently could both pass get_suggestion()'s
        # PENDING check, both save, and both think they'd applied it.
        # The claim is a conditional UPDATE (same claim-then-act shape as
        # SuggestionRejectView/SuggestionWithdrawView): only the first to
        # actually commit wins, since Postgres blocks a second concurrent
        # UPDATE of the same row until the first's transaction resolves,
        # then re-evaluates the WHERE clause against its result.
        revision_field = "link_applied_revision" if suggestion.resulting_person_id else "applied_revision"
        with transaction.atomic():
            response = super().form_valid(form)
            new_object = self.object
            if is_step_one:
                claimed = Suggestion.objects.filter(
                    pk=suggestion.pk, status=Suggestion.Status.PENDING, resulting_person_id__isnull=True
                ).update(resulting_person=new_object)
            else:
                claimed = Suggestion.objects.filter(
                    pk=suggestion.pk, status=Suggestion.Status.PENDING
                ).update(
                    status=Suggestion.Status.APPROVED,
                    reviewed_by=self.request.user,
                    reviewed_at=timezone.now(),
                )
            if not claimed:
                transaction.set_rollback(True)

        if not claimed:
            messages.error(self.request, "This suggestion was already reviewed.")
            return redirect("family:suggestions")

        if is_step_one:
            self._link_applied_revision(suggestion, new_object, "applied_revision")
            if suggestion.link_kind == Suggestion.LinkKind.SPOUSE:
                next_url = f"{reverse('family:union_create', args=[suggestion.link_with.uuid])}?suggestion={suggestion.uuid}"
                messages.success(
                    self.request, f"{new_object.display_name} added - now record the union with their spouse."
                )
            else:
                relation = "father" if suggestion.link_kind == Suggestion.LinkKind.FATHER else "mother"
                next_url = f"{reverse('family:person_update', args=[suggestion.link_with.uuid])}?suggestion={suggestion.uuid}"
                messages.success(
                    self.request,
                    f"{new_object.display_name} added - now confirm them as "
                    f"{suggestion.link_with.display_name}'s {relation}.",
                )
            return redirect(next_url)

        # Finalizing: either a plain single-step suggestion, or the
        # second leg of a link (resulting_person_id already set from
        # step 1 - see is_step_one above, which only ever fires once).
        self._link_applied_revision(suggestion, new_object, revision_field)
        messages.success(self.request, "Suggestion applied.")
        return response
