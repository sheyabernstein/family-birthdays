import pytest

from accounts.models import Account
from tenants.models import Family, FamilyMembership

pytestmark = pytest.mark.django_db


def _member(family, role):
    account = Account.objects.create_user(email=f"{role}@example.com")
    FamilyMembership.objects.create(account=account, family=family, role=role)
    return account


def _login_as(client, account, family):
    client.force_login(account)
    session = client.session
    session["family_id"] = family.id
    session.save()


@pytest.mark.parametrize(
    ["role", "owner_editor_section_shown"],
    [
        [FamilyMembership.Role.OWNER, True],
        [FamilyMembership.Role.EDITOR, True],
        [FamilyMembership.Role.MEMBER, False],
    ],
    ids=[
        "owner sees the owner/editor section",
        "editor sees the owner/editor section",
        "member does not see the owner/editor section",
    ],
)
def test_help_page_gates_owner_editor_section_by_role(client, role, owner_editor_section_shown):
    family = Family.objects.create(name="Test Family")
    account = _member(family, role)
    _login_as(client, account, family)

    resp = client.get("/help/")

    assert (b'id="contact-info"' in resp.content) == owner_editor_section_shown
    assert (b'id="person-visibility"' in resp.content) == owner_editor_section_shown


@pytest.mark.parametrize(
    ["role"],
    [
        [FamilyMembership.Role.OWNER],
        [FamilyMembership.Role.EDITOR],
        [FamilyMembership.Role.MEMBER],
    ],
    ids=["owner", "editor", "member"],
)
def test_help_page_shows_the_viewers_own_role_to_everyone(client, role):
    family = Family.objects.create(name="Test Family")
    account = _member(family, role)
    _login_as(client, account, family)

    resp = client.get("/help/")

    assert f"You currently have the <strong>{role.label}</strong> role".encode() in resp.content
    # The Roles section itself (what each role can do) is never gated -
    # everyone benefits from knowing what each role can do, not just
    # owners/editors.
    assert b'id="roles"' in resp.content


@pytest.mark.parametrize(
    ["role"],
    [
        [FamilyMembership.Role.OWNER],
        [FamilyMembership.Role.EDITOR],
        [FamilyMembership.Role.MEMBER],
    ],
    ids=["owner", "editor", "member"],
)
def test_help_page_shows_the_family_reach_section_to_everyone(client, role):
    # Immediate/direct family come up on My Notifications, a page every
    # role uses - the canonical definition isn't owner/editor-gated.
    family = Family.objects.create(name="Test Family")
    account = _member(family, role)
    _login_as(client, account, family)

    resp = client.get("/help/")

    assert b'id="family-reach"' in resp.content


@pytest.mark.parametrize(
    ["role", "nobody_bullet_shown"],
    [
        [FamilyMembership.Role.OWNER, True],
        [FamilyMembership.Role.EDITOR, True],
        [FamilyMembership.Role.MEMBER, False],
    ],
    ids=[
        "owner sees the nobody-but-owners/editors bullet",
        "editor sees the nobody-but-owners/editors bullet",
        "member does not see it - only owners/editors can set that anyway",
    ],
)
def test_help_page_family_reach_section_hides_the_visibility_only_bullet_from_members(
    client, role, nobody_bullet_shown
):
    family = Family.objects.create(name="Test Family")
    account = _member(family, role)
    _login_as(client, account, family)

    resp = client.get("/help/")

    assert (b"nobody</strong> but owners/editors" in resp.content) == nobody_bullet_shown


def test_help_page_works_before_joining_any_family(client):
    account = Account.objects.create_user(email="new@example.com")
    client.force_login(account)

    resp = client.get("/help/")

    assert resp.status_code == 200
    assert b"You currently have the" not in resp.content
