from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import ListView, TemplateView

from config.logging_config import logger
from tenants.forms import FamilySenderSettingsForm
from tenants.mixins import FamilyEditorRequiredMixin
from tenants.models import Family, FamilyMembership


class CreateFamilyView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = ["tenants.add_family"]

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, "tenants/create_family.html", {"form": FamilySenderSettingsForm()})

    def post(self, request: HttpRequest) -> HttpResponse:
        name = request.POST.get("name", "").strip()
        form = FamilySenderSettingsForm(request.POST)
        if name and form.is_valid():
            family: Family = form.save(commit=False)
            family.name = name
            family.save()
            FamilyMembership.objects.create(
                account=request.user, family=family, role=FamilyMembership.Role.OWNER
            )
            request.session["family_id"] = family.id
            logger.info("family created", family=family.uuid, account=request.user.uuid)
            messages.success(request, f"Created {family.name}.")
            return redirect("family:dashboard")
        if not name:
            messages.error(request, "Give your family a name.")
        return render(request, "tenants/create_family.html", {"form": form, "name": name})


class SwitchFamilyView(LoginRequiredMixin, View):
    def _no_memberships_redirect(self, request: HttpRequest) -> HttpResponse:
        # Same split as FamilyRequiredMixin, for someone who lands here
        # directly (e.g. a stale bookmark) rather than via that redirect.
        if request.user.has_perm("tenants.add_family"):
            return redirect("tenants:create_family")
        return redirect("tenants:no_family_access")

    def get(self, request: HttpRequest) -> HttpResponse:
        memberships = FamilyMembership.objects.filter(account=request.user).select_related("family")
        if not memberships.exists():
            return self._no_memberships_redirect(request)
        return render(request, "tenants/switch_family.html", {"memberships": memberships})

    def post(self, request: HttpRequest) -> HttpResponse:
        memberships = FamilyMembership.objects.filter(account=request.user).select_related("family")
        if not memberships.exists():
            return self._no_memberships_redirect(request)

        family_uuid = request.POST.get("family_id")
        membership = memberships.filter(family__uuid=family_uuid).first()
        if membership:
            request.session["family_id"] = membership.family_id
            return redirect("family:dashboard")

        messages.error(request, "You're not a member of that family.")
        return render(request, "tenants/switch_family.html", {"memberships": memberships})


class FamilySettingsView(FamilyEditorRequiredMixin, UserPassesTestMixin, ListView):
    """Member list and sender branding settings, with per-method permission checks.

    Viewing (FamilyEditorRequiredMixin) is owner/editor only, matching the
    nav's own "Workspace Settings" link. *Changing* sender branding is a
    further, narrower gate: the global tenants.change_family permission
    (a site admin grant - see CreateFamilyView's tenants.add_family for
    the same concept), not a family role - so an owner without that grant
    sees the branding fields read-only, same as an editor. See AGENTS.md's
    Roles/permissions section for why UserPassesTestMixin is layered on
    top of FamilyEditorRequiredMixin here rather than either alone.
    """

    template_name = "tenants/family_settings.html"
    context_object_name = "memberships"
    permission_denied_message = "Only site admins can change workspace settings."

    def test_func(self) -> bool:
        return self.request.method != "POST" or self.request.user.has_perm("tenants.change_family")

    def get_queryset(self) -> QuerySet[FamilyMembership]:
        # Unfiltered prefetch - a filtered Prefetch object would poison
        # this same cache for Account.linked_person's own use elsewhere.
        return (
            FamilyMembership.objects.filter(family=self.request.family)
            .select_related("account")
            .prefetch_related("account__people")
        )

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault("sender_form", FamilySenderSettingsForm(instance=self.request.family))
        # person_in_family(), not linked_person - never ambiguous within
        # one family even if the account is tracked in others too.
        for membership in context["memberships"]:
            membership.resolved_person = membership.account.person_in_family(self.request.family.id)
        return context

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        form = FamilySenderSettingsForm(request.POST, instance=request.family)
        if form.is_valid():
            form.save()
            messages.success(request, "Saved changes to workspace settings.")
            return redirect("tenants:family_settings")
        self.object_list = self.get_queryset()
        return self.render_to_response(self.get_context_data(sender_form=form))


class NoFamilyAccessView(LoginRequiredMixin, TemplateView):
    """Where FamilyRequiredMixin sends a signed-in account with no usable family access.

    That's a signed-in account with no family membership and no
    tenants.add_family permission - there's nothing self-service to do
    here (see AGENTS.md), so this just explains that plainly instead of
    bouncing them into a 403 from CreateFamilyView. Login-required only,
    not FamilyRequiredMixin - that would just redirect right back here.
    """

    template_name = "tenants/no_family_access.html"
