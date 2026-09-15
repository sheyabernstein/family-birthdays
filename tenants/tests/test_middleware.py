import pytest
from django.http import HttpResponse

from accounts.models import Account
from tenants.middleware import CurrentFamilyMiddleware
from tenants.models import Family, FamilyMembership

pytestmark = pytest.mark.django_db


def test_switch_family_link_hidden_with_only_one_membership(client):
    account = Account.objects.create_user(email="single@example.com")
    FamilyMembership.objects.create(account=account, family=Family.objects.create(name="Only Family"))
    client.force_login(account)

    resp = client.get("/")

    assert "Switch Workspace" not in resp.content.decode()


def test_switch_family_link_shown_with_two_memberships(client, two_families):
    family_a, family_b, account_a, _ = two_families
    FamilyMembership.objects.create(account=account_a, family=family_b)
    client.force_login(account_a)
    session = client.session
    session["family_id"] = family_a.id
    session.save()

    resp = client.get("/")

    assert "Switch Workspace" in resp.content.decode()


@pytest.mark.parametrize(
    ["role", "expected_can_edit", "expected_can_delete"],
    [
        [FamilyMembership.Role.OWNER, True, True],
        [FamilyMembership.Role.EDITOR, True, False],
        [FamilyMembership.Role.MEMBER, False, False],
    ],
    ids=[
        "owner gets edit and delete permissions",
        "editor gets edit but not delete permissions",
        "member gets neither",
    ],
)
def test_middleware_resolves_family_permissions_from_role(rf, role, expected_can_edit, expected_can_delete):
    account = Account.objects.create_user(email=f"{role}@example.com")
    family = Family.objects.create(name="Test Family")
    FamilyMembership.objects.create(account=account, family=family, role=role)
    request = rf.get("/")
    request.user = account
    request.session = {}
    middleware = CurrentFamilyMiddleware(lambda r: HttpResponse())

    middleware(request)

    assert request.family_permissions.can_edit == expected_can_edit
    assert request.family_permissions.can_delete == expected_can_delete


def test_middleware_gives_no_permissions_without_a_resolved_membership(rf):
    account = Account.objects.create_user(email="nobody@example.com")
    request = rf.get("/")
    request.user = account
    request.session = {}
    middleware = CurrentFamilyMiddleware(lambda r: HttpResponse())

    middleware(request)

    assert request.family_permissions.can_edit is False
    assert request.family_permissions.can_delete is False
