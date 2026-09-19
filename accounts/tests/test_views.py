import pytest
from django.conf import settings
from django.core import mail
from django.urls import reverse

from accounts import magic_links
from accounts.helpers import EmailValidationResult
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


def test_account_can_update_their_own_contact_info(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(
        "/notifications/settings/",
        {
            "email": "new-address@example.com",
            "phone": "+15559990000",
            "email_notifications_enabled": "on",
            "sms_notifications_enabled": "on",
        },
    )

    assert resp.status_code == 302
    owner.refresh_from_db()
    assert owner.email == "new-address@example.com"
    assert owner.phone == "+15559990000"


def test_account_settings_unchecking_a_channel_turns_it_off(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post("/notifications/settings/", {"email": "owner@example.com"})

    assert resp.status_code == 302
    owner.refresh_from_db()
    assert owner.email_notifications_enabled is False
    assert owner.sms_notifications_enabled is False


def test_account_settings_rejects_an_email_already_used_by_another_account(client, family):
    Account.objects.create_user(email="taken@example.com")
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post("/notifications/settings/", {"email": "taken@example.com"})

    assert resp.status_code == 302
    owner.refresh_from_db()
    assert owner.email != "taken@example.com"


def test_account_settings_rejects_clearing_both_email_and_phone(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post("/notifications/settings/", {})

    assert resp.status_code == 302
    owner.refresh_from_db()
    assert owner.email == "owner@example.com"


def test_request_magic_link_get_renders_the_form(client):
    resp = client.get(reverse("accounts:request_link"))

    assert resp.status_code == 200
    assert b'name="identifier"' in resp.content


def test_request_magic_link_emails_a_sign_in_link_for_a_known_email(client):
    Account.objects.create_user(email="known@example.com")

    resp = client.post(reverse("accounts:request_link"), {"identifier": "known@example.com"})

    assert resp.status_code == 200
    sent = mail.outbox[-1]
    assert sent.to == ["known@example.com"]
    assert "/accounts/login/" in sent.body


def test_request_magic_link_uses_site_base_url_not_the_request(client, settings):
    # request.build_absolute_uri() derives its scheme/host from the
    # request itself, which is only ever accurate if Django itself
    # terminates TLS - this app never does (TLS is terminated upstream by
    # a reverse proxy, see AGENTS.md), so that always produced a plain
    # http://testserver-shaped link regardless of how the site's actually
    # served. The link now comes from notifications.services.absolute_url,
    # which reads settings.SITE_BASE_URL directly - proven here by
    # asserting the link matches that setting even though the test
    # client's own request comes in as plain http://testserver.
    settings.SITE_BASE_URL = "https://family.example.com"
    Account.objects.create_user(email="known@example.com")

    client.post(reverse("accounts:request_link"), {"identifier": "known@example.com"})

    sent = mail.outbox[-1]
    assert "https://family.example.com" in sent.body
    assert "testserver" not in sent.body


def test_request_magic_link_email_includes_an_html_alternative_with_the_link(client):
    Account.objects.create_user(email="known@example.com")

    client.post(reverse("accounts:request_link"), {"identifier": "known@example.com"})

    sent = mail.outbox[-1]
    assert len(sent.alternatives) == 1
    html, mimetype = sent.alternatives[0]
    assert mimetype == "text/html"
    assert "/accounts/login/" in html
    assert "15" in html


def test_request_magic_link_email_omits_the_manage_notifications_link(client):
    # Unlike every other email this app sends, a sign-in link isn't tied
    # to any one family (an Account can belong to several) and is sent
    # before someone's necessarily confirmed access - see AGENTS.md.
    Account.objects.create_user(email="known@example.com")

    client.post(reverse("accounts:request_link"), {"identifier": "known@example.com"})

    sent = mail.outbox[-1]
    html, _ = sent.alternatives[0]
    assert "Manage notification settings" not in html
    assert "Manage notification settings" not in sent.body


def test_request_magic_link_email_is_branded_when_the_account_has_exactly_one_family(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)

    client.post(reverse("accounts:request_link"), {"identifier": owner.email})

    sent = mail.outbox[-1]
    assert sent.from_email == f"{family.name} <{family.sender_email}>"
    html, _ = sent.alternatives[0]
    assert family.name in html
    assert f"Sent by {family.name}." in html


def test_request_magic_link_email_falls_back_to_the_default_brand_for_a_multi_family_account(client):
    account = Account.objects.create_user(email="multi@example.com")
    for name in ["Rokach Family", "Bernstein Family"]:
        fam = Family.objects.create(name=name)
        FamilyMembership.objects.create(account=account, family=fam, role=FamilyMembership.Role.OWNER)

    client.post(reverse("accounts:request_link"), {"identifier": "multi@example.com"})

    sent = mail.outbox[-1]
    assert sent.from_email == f"Family Tree <{settings.DEFAULT_FROM_EMAIL}>"
    html, _ = sent.alternatives[0]
    assert "Rokach Family" not in html
    assert "Bernstein Family" not in html
    assert "Sent by Family Tree." in html


def test_request_magic_link_texts_a_sign_in_link_for_a_known_phone(client, monkeypatch):
    Account.objects.create_user(phone="+15551234567")
    calls = []
    monkeypatch.setattr(
        "accounts.views.send_sms",
        lambda to, body, event_type="", sender_id="": calls.append((to, body)),
    )

    resp = client.post(reverse("accounts:request_link"), {"identifier": "+15551234567"})

    assert resp.status_code == 200
    assert len(calls) == 1
    to, body = calls[0]
    assert to == "+15551234567"
    assert "/accounts/login/" in body
    assert "15" in body


def test_request_magic_link_is_silent_for_an_unknown_identifier(client):
    # Same response whether or not the identifier matched a real account -
    # don't leak which emails/phones are registered.
    resp = client.post(reverse("accounts:request_link"), {"identifier": "nobody@example.com"})

    assert resp.status_code == 200
    assert len(mail.outbox) == 0


def test_link_sent_page_shows_the_identifier_that_was_entered(client):
    Account.objects.create_user(email="known@example.com")

    resp = client.post(reverse("accounts:request_link"), {"identifier": "known@example.com"})

    assert b"known@example.com" in resp.content


def test_link_sent_page_shows_an_unknown_identifier_too(client):
    # Echoing it back is safe either way - it's just what they themselves
    # typed, not confirmation that it matched a real account. Showing it
    # only for a known identifier would itself leak which ones are
    # registered, defeating the point of the silent-either-way response
    # above.
    resp = client.post(reverse("accounts:request_link"), {"identifier": "nobody@example.com"})

    assert b"nobody@example.com" in resp.content


def test_link_sent_page_shows_the_real_ttl_not_a_hardcoded_one(client, monkeypatch):
    # This page used to say "valid for 15 minutes" as a plain string
    # literal, disconnected from magic_links.TOKEN_TTL_SECONDS - the same
    # bug already fixed for the sign-in email/SMS themselves (see
    # AGENTS.md). Proven here by changing the real constant and checking
    # the page actually reflects it, not just that some number appears.
    monkeypatch.setattr(magic_links, "TOKEN_TTL_SECONDS", 42 * 60)

    resp = client.post(reverse("accounts:request_link"), {"identifier": "nobody@example.com"})

    assert b"42 minutes" in resp.content


def test_request_magic_link_is_silent_for_an_inactive_account(client):
    Account.objects.create_user(email="inactive@example.com", is_active=False)

    resp = client.post(reverse("accounts:request_link"), {"identifier": "inactive@example.com"})

    assert resp.status_code == 200
    assert len(mail.outbox) == 0


def test_request_magic_link_stops_sending_once_rate_limited(client):
    Account.objects.create_user(email="frequent@example.com")

    for _ in range(magic_links.RATE_LIMIT_MAX_PER_WINDOW + 2):
        client.post(reverse("accounts:request_link"), {"identifier": "frequent@example.com"})

    assert len(mail.outbox) == magic_links.RATE_LIMIT_MAX_PER_WINDOW


def test_verify_magic_link_logs_in_with_a_valid_token(client):
    account = Account.objects.create_user(email="verify@example.com")
    token = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=Account.Channel.EMAIL, destination=account.email
    )

    resp = client.get(reverse("accounts:verify", args=[token]))

    assert resp.status_code == 302
    assert resp.url == reverse("family:dashboard")
    assert client.session["_auth_user_id"] == str(account.pk)


def test_verify_magic_link_token_is_single_use(client):
    account = Account.objects.create_user(email="onceonly@example.com")
    token = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=Account.Channel.EMAIL, destination=account.email
    )
    client.get(reverse("accounts:verify", args=[token]))
    client.logout()

    resp = client.get(reverse("accounts:verify", args=[token]))

    assert resp.status_code == 400


def test_verify_magic_link_rejects_an_unknown_token(client):
    resp = client.get(reverse("accounts:verify", args=["not-a-real-token"]))

    assert resp.status_code == 400


def test_verify_magic_link_rejects_a_token_for_a_deactivated_account(client):
    account = Account.objects.create_user(email="deactivated@example.com")
    token = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=Account.Channel.EMAIL, destination=account.email
    )
    account.is_active = False
    account.save(update_fields=["is_active"])

    resp = client.get(reverse("accounts:verify", args=[token]))

    assert resp.status_code == 400


def test_verify_magic_link_redirects_an_already_authenticated_user_for_a_spent_token(client):
    # A double click, or a mail client prefetching the link before the
    # person themselves clicks it - not a real problem once the first
    # visit already signed this session in.
    account = Account.objects.create_user(email="already-in@example.com")
    token = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=Account.Channel.EMAIL, destination=account.email
    )
    client.get(reverse("accounts:verify", args=[token]))

    resp = client.get(reverse("accounts:verify", args=[token]))

    assert resp.status_code == 302
    assert resp.url == reverse("family:dashboard")


def test_verify_magic_link_switches_account_even_when_already_authenticated(client):
    # A still-live token must always be consumed and always switch the
    # session to the account it belongs to - never silently kept alive
    # just because the current session happens to be signed in as
    # someone else already.
    other_account = Account.objects.create_user(email="other@example.com")
    client.force_login(other_account)
    account = Account.objects.create_user(email="target@example.com")
    token = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=Account.Channel.EMAIL, destination=account.email
    )

    resp = client.get(reverse("accounts:verify", args=[token]))

    assert resp.status_code == 302
    assert client.session["_auth_user_id"] == str(account.pk)

    # And the token is now spent - a second visit (as the now-logged-in
    # target account) gets the "already authenticated" redirect, not a
    # second real login.
    resp2 = client.get(reverse("accounts:verify", args=[token]))
    assert resp2.status_code == 302


def test_logout_logs_out_the_current_user(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(reverse("accounts:logout"))

    assert resp.status_code == 302
    assert resp.url == reverse("accounts:request_link")
    assert "_auth_user_id" not in client.session


def test_logout_requires_login(client):
    resp = client.post(reverse("accounts:logout"))

    assert resp.status_code == 302
    assert resp.url.startswith(reverse("accounts:request_link"))
    assert "next=" in resp.url


def test_validate_email_requires_login(client):
    resp = client.post(reverse("accounts:validate_email"), {"address": "someone@example.com"})

    assert resp.status_code == 302
    assert resp.url.startswith(reverse("accounts:request_link"))


def test_validate_email_rejects_a_blank_address(client, family):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)

    resp = client.post(reverse("accounts:validate_email"), {"address": "  "})

    assert resp.status_code == 400


def test_validate_email_returns_the_helpers_result_as_json(client, family, monkeypatch):
    owner = _member(family, FamilyMembership.Role.OWNER)
    _login_as(client, owner, family)
    monkeypatch.setattr(
        "accounts.views.validate_email_address",
        lambda address: EmailValidationResult(
            address=address, valid=False, message="Enter a valid email address.", suggestions=["a@gmail.com"]
        ),
    )

    resp = client.post(reverse("accounts:validate_email"), {"address": "a@gmial.com"})

    assert resp.status_code == 200
    assert resp.json() == {
        "valid": False,
        "message": "Enter a valid email address.",
        "suggestions": ["a@gmail.com"],
    }
