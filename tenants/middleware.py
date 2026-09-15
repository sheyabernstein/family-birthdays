from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from tenants.models import FamilyMembership
from tenants.permissions import resolve_family_permissions


class CurrentFamilyMiddleware:
    """Resolves request.family, request.family_role, and request.family_permissions from the session.

    Auto-selects when membership is unambiguous (exactly one family).
    Leaves family/family_role as None (and family_permissions as
    tenants.permissions.NONE) when there's no membership yet (first
    login) or more than one and none chosen - views that need a family
    use the FamilyRequiredMixin (or FamilyEditorRequiredMixin/
    FamilyOwnerRequiredMixin for role-gated views), which redirects to
    the right onboarding or switcher page in either case.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.family = None
        request.family_role = None
        request.has_multiple_families = False
        if request.user.is_authenticated:
            membership = self._resolve(request)
            if membership:
                request.family = membership.family
                request.family_role = membership.role
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
