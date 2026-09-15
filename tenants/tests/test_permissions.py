import pytest

from tenants.models import FamilyMembership
from tenants.permissions import resolve_family_permissions


@pytest.mark.parametrize(
    ["role", "expected_can_edit", "expected_can_delete"],
    [
        [FamilyMembership.Role.OWNER, True, True],
        [FamilyMembership.Role.EDITOR, True, False],
        [FamilyMembership.Role.MEMBER, False, False],
        [None, False, False],
    ],
    ids=[
        "owner can edit and delete",
        "editor can edit but not delete",
        "member can neither edit nor delete",
        "no resolved role can neither edit nor delete",
    ],
)
def test_resolve_family_permissions(role, expected_can_edit, expected_can_delete):
    permissions = resolve_family_permissions(role)

    assert permissions.can_edit == expected_can_edit
    assert permissions.can_delete == expected_can_delete
