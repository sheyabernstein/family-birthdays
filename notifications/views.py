from typing import Any

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import models
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from family.access import person_is_visible, union_is_visible
from family.models import Person, Union
from notifications.audience import available_channels, preference_status
from notifications.enums import ChannelEnum
from notifications.forms import BroadcastForm
from notifications.models import Broadcast, EventType, NotificationPreference, Occurrence
from notifications.tasks import SMS_CHAR_BUDGET, _render_occurrence_message
from tenants.mixins import FamilyEditorRequiredMixin, FamilyRequiredMixin, FamilyScopedMixin


@method_decorator(staff_member_required, name="dispatch")
class ScheduledTasksView(TemplateView):
    """Read-only view of the site's periodic tasks.

    Staff only, since this is site-operator ops info, not something any
    family owner manages. RedBeat keeps the actual schedule in Redis
    with no admin UI of its own (see CELERY_BEAT_SCHEDULER in
    config/settings.py), so this just introspects the static
    CELERY_BEAT_SCHEDULE dict that feeds it.
    """

    template_name = "notifications/scheduled_tasks.html"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["schedule"] = [
            {"name": name, "task": entry["task"], "schedule": str(entry["schedule"])}
            for name, entry in settings.CELERY_BEAT_SCHEDULE.items()
        ]
        return context


class SubscriptionsView(FamilyRequiredMixin, TemplateView):
    template_name = "notifications/subscriptions.html"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        account = self.request.user
        context["account"] = account

        my_channels = [code for code, _destination in available_channels(account)]
        event_types = EventType.objects.filter(
            models.Q(family__isnull=True) | models.Q(family=self.request.family)
        )

        # One row per event type showing the account's circle setting for
        # it (everyone / immediate family only / direct family only /
        # muted) - whatever isn't explicitly set here falls back to
        # EventType.default_state.
        whole_type_overrides = {
            (p.event_type_id, p.channel): p
            for p in NotificationPreference.objects.filter(
                account=account, person__isnull=True, union__isnull=True
            )
        }
        event_type_rows = []
        for event_type in event_types:
            channels_info = []
            for code in my_channels:
                override = whole_type_overrides.get((event_type.id, code))
                state = override.state if override else event_type.default_state
                channels_info.append(
                    {
                        "code": code,
                        "label": ChannelEnum(code).label,
                        "state": state,
                        "is_override": override is not None,
                    }
                )
            event_type_rows.append(
                {
                    "event_type": event_type,
                    "channels": channels_info,
                    # Which states this event type's own dropdown should
                    # offer at all - see EventType.allowed_states (e.g.
                    # DIRECT_FAMILY_ONLY isn't offered for a
                    # union-anchored type like Anniversary).
                    "allowed_states": event_type.allowed_states,
                }
            )

        context["event_type_rows"] = event_type_rows
        context["has_any_channel"] = bool(my_channels)
        # Person/union-specific exceptions to whatever the row above says -
        # these always win, in either direction.
        context["overrides"] = (
            NotificationPreference.objects.filter(account=account)
            .exclude(person__isnull=True, union__isnull=True)
            .select_related("event_type", "person", "union", "union__person_a", "union__person_b")
            .order_by("event_type__name")
        )
        return context


class UpdateEventTypePreferenceView(FamilyRequiredMixin, View):
    """Sets the whole-event-type circle setting from My Notifications.

    Everyone subscribed, immediate family only, or muted entirely - or
    reset back to whatever the event type's own default is.
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        event_type = get_object_or_404(EventType, uuid=request.POST.get("event_type_id"))
        if event_type.family_id not in (None, request.family.id):
            # Global event types (family_id is None) are shared; anything
            # else must belong to this family - an EventType's uuid alone
            # only stops guessing, not a stale link from before this
            # account left a family it used to belong to.
            raise Http404
        channel = request.POST.get("channel")

        if request.POST.get("action") == "reset":
            NotificationPreference.objects.filter(
                account=request.user,
                event_type=event_type,
                channel=channel,
                person__isnull=True,
                union__isnull=True,
            ).delete()
            messages.success(request, f"Reset {event_type.name.lower()} ({channel}) to the family default.")
        else:
            state = request.POST.get("state")
            if state not in event_type.allowed_states:
                raise Http404
            NotificationPreference.objects.update_or_create(
                account=request.user,
                event_type=event_type,
                channel=channel,
                person=None,
                union=None,
                defaults={"state": state},
            )
            messages.success(request, f"Updated {event_type.name.lower()} notifications ({channel}).")

        return redirect(request.POST.get("next") or "notifications:subscriptions")


class TogglePersonPreferenceView(FamilyRequiredMixin, View):
    """The per-person/union override shown on a person's own page.

    Shown on family.views.PersonDetailView and in the overrides list on
    My Notifications. A single toggle: if this person/union already has an
    explicit override, remove it (falling back to whatever the event
    type's circle setting says); otherwise add one that flips whatever's
    currently in effect - which is exactly the escape hatch for
    "immediate family only, but also this cousin" or "everyone, but not
    this one person".
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        event_type = get_object_or_404(EventType, uuid=request.POST.get("event_type_id"))
        if event_type.family_id not in (None, request.family.id):
            raise Http404
        if event_type.code == EventType.BuiltinCode.BROADCAST:
            # No UI ever offers this (see family.views.PersonDetailView),
            # so reaching here means a crafted request - see
            # NotificationPreference.clean() for the same rule enforced
            # at the model level.
            raise Http404
        channel = request.POST.get("channel")
        person_uuid = request.POST.get("person_id") or None
        union_uuid = request.POST.get("union_id") or None
        if person_uuid and union_uuid:
            # NotificationPreference's own preference_not_both_person_and_union
            # constraint would otherwise turn this into an unhandled 500
            # (IntegrityError) instead of a clean rejection of a request no
            # legitimate form ever sends.
            raise Http404

        person = None
        union = None
        if person_uuid:
            person = get_object_or_404(Person, uuid=person_uuid)
            if not person_is_visible(person, request.family):
                raise Http404
        if union_uuid:
            union = get_object_or_404(Union, uuid=union_uuid)
            if not union_is_visible(union, request.family):
                raise Http404

        existing = NotificationPreference.objects.filter(
            account=request.user,
            person=person,
            union=union,
            event_type=event_type,
            channel=channel,
        ).first()

        subject = person or union

        if existing:
            existing.delete()
            messages.success(
                request, f"Reset {event_type.name.lower()} notifications for {subject} ({channel})."
            )
        else:
            currently_subscribed = preference_status(
                request.user, event_type, person=person, union=union, channel=channel
            ).subscribed
            new_state = (
                NotificationPreference.State.MUTED
                if currently_subscribed
                else NotificationPreference.State.SUBSCRIBED
            )
            NotificationPreference.objects.create(
                account=request.user,
                person=person,
                union=union,
                event_type=event_type,
                channel=channel,
                state=new_state,
            )
            verb = "Muted" if new_state == NotificationPreference.State.MUTED else "Subscribed to"
            messages.success(
                request, f"{verb} {event_type.name.lower()} notifications for {subject} ({channel})."
            )

        next_url = request.POST.get("next") or "family:dashboard"
        return redirect(next_url)


class OccurrencePreviewView(FamilyEditorRequiredMixin, FamilyScopedMixin, DetailView):
    """Renders the email/SMS one occurrence will actually produce, for an owner/editor to check.

    Read-only - never creates a Message row or touches is_sent, unlike
    the real send path. Rendered as of the occurrence's own send_date
    (see notifications.tasks._render_occurrence_message's as_of), not
    whenever the preview happens to be requested - an occurrence
    computed weeks ahead would otherwise show the far-future date
    fallback ("was on Sept. 27, 2026") instead of the near-term wording
    ("is on Monday"/"is today") a recipient will actually see once it's
    really sent.

    family_lookup mirrors family.views.DashboardView's own candidate
    filter (person__family, or either side of a union) - the exact set
    of occurrences an owner/editor can already see on Upcoming for their
    own family, nothing broader. Ordinary GET, not POST - this changes
    nothing, so there's no CSRF-relevant state to protect.
    """

    model = Occurrence
    queryset = Occurrence.objects.with_related()
    template_name = "notifications/occurrence_preview.html"
    context_object_name = "occurrence"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    family_lookup = ["person__family", "union__person_a__family", "union__person_b__family"]

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        occurrence = self.object
        as_of = occurrence.send_date
        subject, _body, html = _render_occurrence_message(occurrence, channel=ChannelEnum.EMAIL, as_of=as_of)
        _subject, sms_text, _html = _render_occurrence_message(
            occurrence, channel=ChannelEnum.SMS, as_of=as_of
        )
        context.update(
            {
                "preview_as_of": as_of,
                "email_subject": subject,
                "email_html": html,
                "sms_text": sms_text,
                "sms_char_budget": SMS_CHAR_BUDGET,
            }
        )
        return context


def _own_or_sent_q(request: HttpRequest) -> models.Q:
    """Builds the visibility filter for who may see a broadcast.

    A sent broadcast is family history - any owner/editor can see it.
    A still-pending one is only visible to whoever created it, or a
    Django superuser - so one editor can't see (or edit/cancel) a draft
    another editor scheduled but hasn't sent yet. Shared by
    _visible_broadcasts (list/create) and BroadcastUpdateView/
    BroadcastDeleteView's own get_queryset() (which get the tenant-
    boundary half of the filter from FamilyScopedMixin instead - see its
    docstring for why this one's layered on top rather than folded in).
    """
    if request.user.is_superuser:
        return models.Q()
    return models.Q(is_sent=True) | models.Q(created_by=request.user)


def _visible_broadcasts(request: HttpRequest) -> QuerySet[Broadcast]:
    return Broadcast.objects.filter(family=request.family).filter(_own_or_sent_q(request))


class BroadcastListView(FamilyEditorRequiredMixin, ListView):
    """Lists this family's broadcasts, split into scheduled and sent.

    Owner/editor only - both this family's already-sent broadcasts
    (visible to everyone with access, as history) and its not-yet-sent
    ones (visible only to whoever created them, or a superuser - see
    _visible_broadcasts). Creating one happens on the same page (see
    broadcast_list.html) via BroadcastForm, rather than a separate
    "new" page - there's only one field group, so a modal/inline form
    is simpler than a whole extra view+template.

    Split into scheduled_broadcasts/sent_broadcasts here, not filtered
    in the template off one flat list - each half wants the opposite
    order: scheduled is soonest-first (what's coming up next), sent is
    most-recent-first (Broadcast.Meta.ordering's own "-send_at", right
    for a history log) - simplest to just reverse the one queryset
    Python-side rather than expressing two orderings in one query.
    """

    model = Broadcast
    template_name = "notifications/broadcast_list.html"
    context_object_name = "broadcasts"

    def get_queryset(self) -> QuerySet[Broadcast]:
        return _visible_broadcasts(self.request).prefetch_related("people")

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["form"] = BroadcastForm(family=self.request.family)
        broadcasts = context["broadcasts"]
        context["scheduled_broadcasts"] = list(reversed([b for b in broadcasts if not b.is_sent]))
        context["sent_broadcasts"] = [b for b in broadcasts if b.is_sent]
        return context


class BroadcastCreateView(FamilyEditorRequiredMixin, CreateView):
    """Creates a broadcast. POST-only - there's no separate "new" page to GET.

    The create form lives inline on BroadcastListView's own page instead
    - there's only one field group, so a modal/inline form is simpler
    than a whole extra view+template. On a validation error, form_invalid
    re-renders that same list page with the invalid form's own errors
    attached instead of a blank one.
    """

    model = Broadcast
    form_class = BroadcastForm
    template_name = "notifications/broadcast_list.html"
    http_method_names = ["post"]

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["family"] = self.request.family
        return kwargs

    def form_valid(self, form: BroadcastForm) -> HttpResponse:
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, "Broadcast scheduled.")
        return response

    def form_invalid(self, form: BroadcastForm) -> HttpResponse:
        # Re-render the list page (not a separate form page - see
        # BroadcastListView) with the invalid form's own errors attached,
        # instead of BroadcastListView's own fresh, blank one.
        context = self.get_context_data()
        context["broadcasts"] = _visible_broadcasts(self.request).prefetch_related("people")
        context["form"] = form
        return self.render_to_response(context)

    def get_success_url(self) -> str:
        return reverse("notifications:broadcast_list")


class BroadcastUpdateView(FamilyScopedMixin, FamilyEditorRequiredMixin, UpdateView):
    """Edits a not-yet-sent broadcast.

    Editable only up to the moment send_due_broadcasts claims it (see
    that task's own docstring for why the claim is safe against this) -
    once is_sent is True it's history, not a draft, so this 404s instead
    of letting an owner/editor "edit" something already delivered. Also
    404s for a pending broadcast someone else created, unless you're a
    superuser - see _own_or_sent_q. FamilyScopedMixin (family_lookup)
    covers the tenant boundary; get_queryset() layers the rest on top.
    """

    model = Broadcast
    form_class = BroadcastForm
    template_name = "notifications/broadcast_form.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    family_lookup = "family"

    def get_queryset(self) -> QuerySet[Broadcast]:
        return super().get_queryset().filter(_own_or_sent_q(self.request), is_sent=False)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["family"] = self.request.family
        return kwargs

    def form_valid(self, form: BroadcastForm) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, "Saved changes to the broadcast.")
        return response

    def get_success_url(self) -> str:
        return reverse("notifications:broadcast_list")


class BroadcastDeleteView(FamilyScopedMixin, FamilyEditorRequiredMixin, DeleteView):
    """Cancels a not-yet-sent broadcast.

    Editor, not owner-only, unlike Person/UnionDeleteView - a broadcast
    is this family's own scheduled
    message, not another person's ledger record, so the same role that
    can create/edit one can also cancel it. Also 404s for a pending
    broadcast someone else created, unless you're a superuser - see
    _own_or_sent_q.
    """

    model = Broadcast
    template_name = "notifications/broadcast_confirm_delete.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    success_url = reverse_lazy("notifications:broadcast_list")
    family_lookup = "family"

    def get_queryset(self) -> QuerySet[Broadcast]:
        return super().get_queryset().filter(_own_or_sent_q(self.request), is_sent=False)

    def form_valid(self, form: forms.Form) -> HttpResponse:
        messages.success(self.request, "Cancelled the broadcast.")
        return super().form_valid(form)
