import uuid

import pytest
from django.contrib.auth.models import Permission

from accounts.models import Account
from tenants.models import Family, FamilyMembership

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "has_permission",
    [True, False],
    ids=["has permission", "doesn't have permission"],
)
def test_creating_a_family_requires_add_family_permission(has_permission, client):
    family_name = str(uuid.uuid4())
    account = Account.objects.create_user(email="founder@example.com")

    permission = Permission.objects.get(
        codename="add_family",
        content_type__app_label="tenants",
    )

    if has_permission:
        account.user_permissions.add(permission)

    client.force_login(account)

    resp = client.post("/family/create/", {"name": family_name})

    if has_permission:
        membership = FamilyMembership.objects.get(
            account=account,
            family__name=family_name,
        )
        assert membership.role == FamilyMembership.Role.OWNER
    else:
        assert resp.status_code == 403
        assert Family.objects.filter(name=family_name).exists() is False


def test_creating_a_family_makes_the_creator_its_owner(client):
    account = Account.objects.create_user(email="founder@example.com")
    account.user_permissions.add(
        Permission.objects.get(
            codename="add_family",
            content_type__app_label="tenants",
        )
    )
    client.force_login(account)

    client.post("/family/create/", {"name": "Founded Family"})

    membership = FamilyMembership.objects.get(account=account)
    assert membership.role == FamilyMembership.Role.OWNER
    assert membership.family.name == "Founded Family"


def test_no_family_access_page_renders_for_a_signed_in_account_with_no_family(client):
    account = Account.objects.create_user(email="new@example.com")
    client.force_login(account)

    resp = client.get("/family/no-family/")

    assert resp.status_code == 200
    assert b"site admin" in resp.content


@pytest.mark.parametrize(
    ["has_permission", "expected_redirect"],
    [
        [True, "/family/create/"],
        [False, "/family/no-family/"],
    ],
    ids=["has add_family permission", "doesn't have add_family permission"],
)
def test_switch_family_with_no_memberships_redirects_by_permission(client, has_permission, expected_redirect):
    account = Account.objects.create_user(email="nobody@example.com")
    if has_permission:
        account.user_permissions.add(
            Permission.objects.get(codename="add_family", content_type__app_label="tenants")
        )
    client.force_login(account)

    resp = client.get("/family/switch/")

    assert resp.status_code == 302
    assert resp.url == expected_redirect


def test_switch_family_posts_by_uuid_not_pk(client):
    account = Account.objects.create_user(email="member@example.com")
    family = Family.objects.create(name="Target Family")
    FamilyMembership.objects.create(account=account, family=family, role=FamilyMembership.Role.MEMBER)
    client.force_login(account)

    resp = client.post("/family/switch/", {"family_id": str(family.uuid)})

    assert resp.status_code == 302
    assert resp.url == "/"
    assert client.session["family_id"] == family.id


def test_switch_family_rejects_a_family_you_are_not_a_member_of(client):
    account = Account.objects.create_user(email="member@example.com")
    own_family = Family.objects.create(name="Own Family")
    other_family = Family.objects.create(name="Other Family")
    FamilyMembership.objects.create(account=account, family=own_family, role=FamilyMembership.Role.MEMBER)
    client.force_login(account)

    resp = client.post("/family/switch/", {"family_id": str(other_family.uuid)})

    assert resp.status_code == 200
    assert client.session.get("family_id") != other_family.id


def test_creating_a_family_saves_sender_branding(client):
    account = Account.objects.create_user(email="founder@example.com")
    account.user_permissions.add(
        Permission.objects.get(codename="add_family", content_type__app_label="tenants")
    )
    client.force_login(account)

    client.post(
        "/family/create/",
        {"name": "Branded Family", "sms_sender_id": "BrandFam", "reply_to_email": "reply@example.com"},
    )

    family = Family.objects.get(name="Branded Family")
    assert family.sms_sender_id == "BrandFam"
    assert family.reply_to_email == "reply@example.com"


def test_creating_a_family_rejects_a_non_alphanumeric_sender_id(client):
    account = Account.objects.create_user(email="founder@example.com")
    account.user_permissions.add(
        Permission.objects.get(codename="add_family", content_type__app_label="tenants")
    )
    client.force_login(account)

    resp = client.post("/family/create/", {"name": "Bad Sender Family", "sms_sender_id": "Bad Sender!"})

    assert resp.status_code == 200
    assert not Family.objects.filter(name="Bad Sender Family").exists()


def _member(family, role):
    account = Account.objects.create_user(email=f"{role}-{family.pk}@example.com")
    FamilyMembership.objects.create(account=account, family=family, role=role)
    return account


def _login_as(client, account, family):
    client.force_login(account)
    session = client.session
    session["family_id"] = family.id
    session.save()


def test_family_settings_shows_the_editable_form_to_an_editor(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.get("/family/settings/")

    assert resp.status_code == 200
    assert b'name="sms_sender_id"' in resp.content


def test_family_settings_shows_read_only_values_to_a_plain_member(client, family):
    family.sms_sender_id = "RokachFam"
    family.save(update_fields=["sms_sender_id"])
    member = _member(family, FamilyMembership.Role.MEMBER)
    _login_as(client, member, family)

    resp = client.get("/family/settings/")

    assert resp.status_code == 200
    assert b'name="sms_sender_id"' not in resp.content
    assert b"RokachFam" in resp.content


def test_family_settings_lets_an_owner_update_sender_branding(client, family):
    owner = _member(family=family, role=FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        "/family/settings/", {"sms_sender_id": "NewSender", "reply_to_email": "reply@example.com"}
    )

    assert resp.status_code == 302
    family.refresh_from_db()
    assert family.sms_sender_id == "NewSender"
    assert family.reply_to_email == "reply@example.com"


@pytest.mark.parametrize(
    ["role"], [[FamilyMembership.Role.EDITOR], [FamilyMembership.Role.MEMBER]], ids=["editor", "member"]
)
def test_family_settings_rejects_updates_from_editor_member(role, client, family):
    member = _member(family=family, role=role)
    _login_as(client, member, family)

    resp = client.post("/family/settings/", {"sms_sender_id": "Hijacked"})

    assert resp.status_code == 403
    family.refresh_from_db()
    assert family.sms_sender_id == ""
