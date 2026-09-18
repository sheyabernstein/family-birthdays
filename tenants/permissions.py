"""Single source of truth for what a family role can do.

Both the view-level guards (tenants.mixins.FamilyEditorRequiredMixin/
FamilyOwnerRequiredMixin) and every template's own role-gated UI check
(the "Edit"/"Remove" buttons, the Help page's role-specific sections)
resolve permissions from here, instead of each independently re-deriving
"owner or editor" / "owner only" from request.family_role - twelve
separate copies of that check scattered across templates and views is
exactly what would eventually drift apart if the role model ever changes.
"""

import dataclasses

from tenants.models import FamilyMembership


@dataclasses.dataclass(frozen=True)
class FamilyPermissions:
    """What the current viewer can do in their current workspace."""

    can_edit: bool
    can_delete: bool


NONE = FamilyPermissions(can_edit=False, can_delete=False)


def resolve_family_permissions(role: str | None) -> FamilyPermissions:
    """Resolves what a family role can do, treating an unresolved role as no permissions at all.

    Role is None when there's no resolved membership yet (first login,
    or an ambiguous multiple-membership session that hasn't picked one -
    see CurrentFamilyMiddleware) - NONE is the correct answer there, not
    an error.
    """
    if role is None:
        return NONE
    return FamilyPermissions(
        can_edit=role in FamilyMembership.EDITOR_ROLES,
        can_delete=role == FamilyMembership.Role.OWNER,
    )
