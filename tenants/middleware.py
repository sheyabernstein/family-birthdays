from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from tenants.models import FamilyMembership
from tenants.permissions import resolve_family_permissions


class CurrentFamilyMiddleware:
    """Resolves request.family, request.family_role, request.family_permissions, and request.self_person from the session.

    Auto-selects when membership is unambiguous (exactly one family).
    Leaves family/family_role as None (and family_permissions as
    tenants.permissions.NONE) when there's no membership yet (first
    login) or more than one and none chosen - views that need a family
    use the FamilyRequiredMixin (or FamilyEditorRequiredMixin/
    FamilyOwnerRequiredMixin for role-gated views), which redirects to
    the right onboarding or switcher page in either case.

    self_person is this account's own Person record within request.family
    (None if there isn't one - an owner who hasn't added themselves to
    their own tree yet is a real, expected state, not an error - see
    accounts/AGENTS.md). Resolved here, not a separate middleware or
    context processor, since it's the same "per-request, family-scoped"
    shape as everything else this middleware already does, and both
    accounts.views.VerifyMagicLinkView (the sign-in redirect) and every
    template (via the already-enabled `request` context processor - see
    templates/base.html's own "My Profile" nav link) need it without a
    second query each.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.family = None
        request.family_role = None
        request.has_multiple_families = False
        request.self_person = None
        if request.user.is_authenticated:
            membership = self._resolve(request)
            if membership:
                request.family = membership.family
                request.family_role = membership.role
                request.self_person = request.family.people.filter(account=request.user).first()
        request.family_permissions = resolve_family_permissions(request.family_role)
        return self.get_response(request)

    def _resolve(self, request: HttpRequest) -> FamilyMembership | None:
        memberships = list(FamilyMembership.objects.filter(account=request.user).select_related("family"))
        request.has_multiple_families = len(memberships) > 1
        if not memberships:
            return None

        family_id = request.session.get("family_id")
        if family_id:
            match = next((m for m in memberships if m.family_id == family_id), None)
            if match:
                return match

        if len(memberships) == 1:
            request.session["family_id"] = memberships[0].family_id
            return memberships[0]

        return None
