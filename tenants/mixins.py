from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from tenants.models import FamilyMembership


class FamilyRequiredMixin(LoginRequiredMixin):
    """Requires login AND a resolved request.family.

    Sends users with unresolved multiple memberships to the switcher, and
    first-time users to whichever of two very different places actually
    applies to them.

    There's no self-service tenant creation (see AGENTS.md) - a brand
    new account can't spin up its own family, only a site admin
    (tenants.add_family, e.g. Django staff) can. So a first-time user
    with no membership at all splits two ways: someone with that
    permission goes to create_family, same as before; everyone else -
    which is the common case, a person a site admin hasn't finished
    setting up yet - goes to no_family_access instead. Redirecting that
    second group to create_family would just trade one dead end (nothing
    to do here) for a worse one (a bare 403 from CreateFamilyView's own
    PermissionRequiredMixin, with no explanation).
    """

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.family is None:
            if FamilyMembership.objects.filter(account=request.user).exists():
                return redirect("tenants:switch_family")
            if request.user.has_perm("tenants.add_family"):
                return redirect("tenants:create_family")
            return redirect("tenants:no_family_access")
        return super().dispatch(request, *args, **kwargs)


class FamilyEditorRequiredMixin(FamilyRequiredMixin):
    """FamilyRequiredMixin, plus: the current membership must be an owner or editor.

    Used for creating/updating people and unions. See tenants.permissions
    for the single source of truth this (and every role-gated template
    check) resolves against.
    """

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.family is not None and not request.family_permissions.can_edit:
            raise PermissionDenied("Only family owners and editors can manage people.")
        return super().dispatch(request, *args, **kwargs)


class FamilyOwnerRequiredMixin(FamilyRequiredMixin):
    """FamilyRequiredMixin, plus: the current membership must be an owner.

    Used for destructive actions like deleting a person. See
    tenants.permissions for the single source of truth this (and every
    role-gated template check) resolves against.
    """

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.family is not None and not request.family_permissions.can_delete:
            raise PermissionDenied("Only the family owner can do this.")
        return super().dispatch(request, *args, **kwargs)


class FamilyScopedMixin:
    """Ensures the specific row a URL points at actually belongs to the viewer's own family.

    FamilyEditorRequiredMixin/FamilyOwnerRequiredMixin only check the
    viewer's *role* - that they can edit/delete something in their own
    family. They say nothing about whether the specific row a URL points
    at (a person's or union's UUID) actually belongs to that family, and
    a role check alone can't: a plain get_queryset() override on each
    view was the only thing standing between an owner/editor and someone
    else's family record reachable by a guessed or shared direct URL.
    That works today (every such view happens to filter correctly), but
    it's pure per-view discipline with nothing to catch a future view
    that forgets - a missing filter fails silently (Model.objects.all(),
    every family's rows) rather than loudly.

    Mix this in and set family_lookup to make that filter unskippable:
    a subclass that doesn't set it raises ImproperlyConfigured the
    moment the class is defined (i.e. at import time - the whole app
    fails to start, not just the one view on its first request), instead
    of quietly serving another family's record.

    family_lookup is either one field path ("family", for a model with
    a single owning family) or a list of them, OR'd together
    (["person_a__family", "person_b__family"], for a model like Union
    that's legitimately editable from either side of a cross-family
    marriage - see UnionUpdateView/UnionDeleteView).

    A view whose correct scoping is more than just "belongs to this
    family" (e.g. Broadcast's pending-visibility rule - see
    notifications.views._own_or_sent_q) still mixes this in for the
    tenant-boundary half, then layers its own extra condition on top of
    super().get_queryset() - see BroadcastUpdateView/BroadcastDeleteView.
    """

    family_lookup: str | list[str] | None = None

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.family_lookup is None:
            raise ImproperlyConfigured(
                f"{cls.__name__} must set family_lookup - see FamilyScopedMixin's docstring."
            )

    def get_queryset(self) -> QuerySet:
        queryset = super().get_queryset()
        lookups = [self.family_lookup] if isinstance(self.family_lookup, str) else self.family_lookup
        family_filter = Q()
        for lookup in lookups:
            family_filter |= Q(**{lookup: self.request.family})
        return queryset.filter(family_filter)
