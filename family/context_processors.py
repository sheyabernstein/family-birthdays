from django.http import HttpRequest

from family.models import Suggestion


def suggestions(request: HttpRequest) -> dict:
    """Powers the nav's "Suggestions" link - a pending count for reviewers, visibility for everyone else.

    Only queries when a family is actually resolved (most requests
    outside the family/notifications/tenants apps have none) - same
    "cheap, per-request, computed once" shape as
    tenants.middleware.CurrentFamilyMiddleware's own self_person/
    family_permissions, just living here instead of there since
    Suggestion is a family-app concept, not a tenants one.
    """
    if not getattr(request, "family", None) or not request.user.is_authenticated:
        return {"pending_suggestion_count": 0, "show_suggestions_nav_link": False}

    if request.family_permissions.can_edit:
        pending_count = Suggestion.objects.filter(
            family=request.family, status=Suggestion.Status.PENDING
        ).count()
        return {"pending_suggestion_count": pending_count, "show_suggestions_nav_link": True}

    has_suggested = Suggestion.objects.filter(family=request.family, submitted_by=request.user).exists()
    return {"pending_suggestion_count": 0, "show_suggestions_nav_link": has_suggested}
