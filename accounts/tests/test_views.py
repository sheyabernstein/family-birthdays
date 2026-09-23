import re

import pytest
from django.conf import settings
from django.core import mail
from django.urls import reverse

from accounts import magic_links
from accounts.helpers import EmailValidationResult
from accounts.models import Account
from family.models import Person
from notifications.enums import ChannelEnum
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


def test_request_magic_link_get_leaves_identifier_blank_by_default(client):
    resp = client.get(reverse("accounts:request_link"))

    assert b'value=""' in resp.content


def test_request_magic_link_get_prefills_identifier_from_the_query_string(client):
    resp = client.get(reverse("accounts:request_link"), {"identifier": "known@example.com"})

    assert b'value="known@example.com"' in resp.content


def test_request_magic_link_get_prefills_identifier_nested_inside_next(client):
    # The common real path: a signed-out visit to a link like the
    # "Manage notification settings" one every notification email
    # carries redirects here via LoginRequiredMixin's own
    # redirect_to_login(), which nests the original URL - identifier and
    # all - under ?next= rather than passing it as a sibling parameter.
    resp = client.get(
        reverse("accounts:request_link"),
        {"next": "/notifications/?identifier=known@example.com"},
    )

    assert b'value="known@example.com"' in resp.content


def test_request_magic_link_get_does_not_auto_submit_a_prefilled_identifier(client):
    Account.objects.create_user(email="known@example.com")

    client.get(reverse("accounts:request_link"), {"identifier": "known@example.com"})

    assert len(mail.outbox) == 0


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


def test_request_magic_link_email_includes_a_code_alternative(client):
    # For whoever's signing in on a different device than the one that
    # received this email, or can't tap the link at all on this one.
    Account.objects.create_user(email="known@example.com")

    client.post(reverse("accounts:request_link"), {"identifier": "known@example.com"})

    sent = mail.outbox[-1]
    html, _ = sent.alternatives[0]
    assert "/accounts/login/code/" in html
    match = re.search(r"<strong[^>]*>([A-Z0-9]{6})</strong>", html)
    assert match is not None
    code = match.group(1)
    assert set(code) <= set(magic_links.CODE_ALPHABET)


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
        "accounts.tasks.send_sms",
        lambda to, body, event_type="", sender_id="": calls.append((to, body)),
    )

    resp = client.post(reverse("accounts:request_link"), {"identifier": "+15551234567"})

    assert resp.status_code == 200
    assert len(calls) == 1
    to, body = calls[0]
    assert to == "+15551234567"
    assert "/accounts/login/" in body
    assert "15" in body


def test_request_magic_link_texts_a_code_alongside_the_link(client, monkeypatch):
    # No code_url here (see the view's own comment) - stays a single SMS
    # segment, and whoever's reading this is expected to already be on
    # (or near) the sign-in page.
    Account.objects.create_user(phone="+15551234567")
    calls = []
    monkeypatch.setattr(
        "accounts.tasks.send_sms",
        lambda to, body, event_type="", sender_id="": calls.append((to, body)),
    )

    client.post(reverse("accounts:request_link"), {"identifier": "+15551234567"})

    _to, body = calls[0]
    assert "Or enter code " in body
    match = re.search(r"Or enter code ([A-Z0-9]{6})\.", body)
    assert match is not None
    assert set(match.group(1)) <= set(magic_links.CODE_ALPHABET)


def test_request_magic_link_texts_a_sign_in_link_for_a_phone_typed_with_punctuation(client, monkeypatch):
    # find_by_identifier normalizes punctuation before matching - this is
    # the end-to-end proof that a number typed the way a person would
    # actually type it (not the bare E.164 the account is stored as)
    # still finds the account and gets texted.
    Account.objects.create_user(phone="+15551234567")
    calls = []
    monkeypatch.setattr(
        "accounts.tasks.send_sms",
        lambda to, body, event_type="", sender_id="": calls.append((to, body)),
    )

    resp = client.post(reverse("accounts:request_link"), {"identifier": "+1 (555) 123-4567"})

    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] == "+15551234567"


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
    token, _code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )

    resp = client.get(reverse("accounts:verify", args=[token]))

    assert resp.status_code == 302
    assert resp.url == reverse("family:home")
    assert client.session["_auth_user_id"] == str(account.pk)


def test_verify_magic_link_redirects_to_the_accounts_own_person_in_the_tree(client):
    # The whole point of the sign-in redirect: land on "where am I in
    # this family" rather than the generic Upcoming feed. Follows both
    # hops (accounts:verify -> family:home -> family:family_tree) -
    # the actual self-person resolution happens on family:home's own
    # fresh request, not within VerifyMagicLinkView itself (see that
    # view's own comment for why: request.self_person would still
    # reflect the pre-login state if read directly there instead).
    account = Account.objects.create_user(email="verify@example.com")
    family = Family.objects.create(name="Test Family")
    FamilyMembership.objects.create(account=account, family=family)
    person = Person.objects.create(family=family, first_name_en="Me", last_name_en="Test", account=account)
    token, _code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )

    resp = client.get(reverse("accounts:verify", args=[token]), follow=True)

    assert resp.redirect_chain == [
        (reverse("family:home"), 302),
        (reverse("family:family_tree", args=[person.uuid]), 302),
    ]
    assert resp.status_code == 200


def test_verify_magic_link_falls_back_to_dashboard_without_a_matching_person(client):
    # A real, single family, but no Person record links this account to
    # it yet (e.g. an owner who created the workspace but hasn't added
    # themselves to their own tree) - nothing to land on instead.
    account = Account.objects.create_user(email="verify@example.com")
    FamilyMembership.objects.create(account=account, family=Family.objects.create(name="Test Family"))
    token, _code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )

    resp = client.get(reverse("accounts:verify", args=[token]), follow=True)

    assert resp.redirect_chain == [
        (reverse("family:home"), 302),
        (reverse("family:dashboard"), 302),
    ]
    assert resp.status_code == 200


def test_verify_magic_link_falls_back_to_the_switcher_with_an_ambiguous_family(client):
    # family:home relies entirely on FamilyRequiredMixin's own dispatch()
    # for this case - request.family is None with 2+ memberships, so
    # it redirects to the switcher before HomeView.get() ever runs, same
    # as any other FamilyRequiredMixin view would.
    account = Account.objects.create_user(email="verify@example.com")
    FamilyMembership.objects.create(account=account, family=Family.objects.create(name="Family A"))
    FamilyMembership.objects.create(account=account, family=Family.objects.create(name="Family B"))
    token, _code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )

    resp = client.get(reverse("accounts:verify", args=[token]), follow=True)

    assert resp.redirect_chain == [
        (reverse("family:home"), 302),
        (reverse("tenants:switch_family"), 302),
    ]
    assert resp.status_code == 200


def test_verify_magic_link_token_is_single_use(client):
    account = Account.objects.create_user(email="onceonly@example.com")
    token, _code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
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
    token, _code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )
    account.is_active = False
    account.save(update_fields=["is_active"])

    resp = client.get(reverse("accounts:verify", args=[token]))

    assert resp.status_code == 400


def test_verify_magic_link_redirects_an_already_authenticated_user_for_a_spent_token(client):
    # A double click, or a mail client prefetching the link before the
    # person themselves clicks it - not a real problem once the first
    # visit already signed this session in. No FamilyMembership at all
    # here, so family:home's own FamilyRequiredMixin sends this on to
    # the no-access page rather than the switcher.
    account = Account.objects.create_user(email="already-in@example.com")
    token, _code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )
    client.get(reverse("accounts:verify", args=[token]))

    resp = client.get(reverse("accounts:verify", args=[token]), follow=True)

    assert resp.redirect_chain == [
        (reverse("family:home"), 302),
        (reverse("tenants:no_family_access"), 302),
    ]
    assert resp.status_code == 200


def test_verify_magic_link_redirects_an_already_authenticated_user_to_their_own_person(client):
    account = Account.objects.create_user(email="already-in@example.com")
    family = Family.objects.create(name="Test Family")
    FamilyMembership.objects.create(account=account, family=family)
    person = Person.objects.create(family=family, first_name_en="Me", last_name_en="Test", account=account)
    token, _code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )
    client.get(reverse("accounts:verify", args=[token]))

    resp = client.get(reverse("accounts:verify", args=[token]), follow=True)

    assert resp.redirect_chain == [
        (reverse("family:home"), 302),
        (reverse("family:family_tree", args=[person.uuid]), 302),
    ]
    assert resp.status_code == 200


def test_verify_magic_link_switches_account_even_when_already_authenticated(client):
    # A still-live token must always be consumed and always switch the
    # session to the account it belongs to - never silently kept alive
    # just because the current session happens to be signed in as
    # someone else already.
    other_account = Account.objects.create_user(email="other@example.com")
    client.force_login(other_account)
    account = Account.objects.create_user(email="target@example.com")
    token, _code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )

    resp = client.get(reverse("accounts:verify", args=[token]))

    assert resp.status_code == 302
    assert client.session["_auth_user_id"] == str(account.pk)

    # And the token is now spent - a second visit (as the now-logged-in
    # target account) gets the "already authenticated" redirect, not a
    # second real login.
    resp2 = client.get(reverse("accounts:verify", args=[token]))
    assert resp2.status_code == 302


def test_verify_code_get_renders_the_form(client):
    resp = client.get(reverse("accounts:verify_code"))

    assert resp.status_code == 200
    assert b'name="identifier"' in resp.content
    assert b'name="code"' in resp.content


def test_verify_code_logs_in_with_a_valid_code(client):
    account = Account.objects.create_user(email="verify@example.com")
    _token, code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )

    resp = client.post(reverse("accounts:verify_code"), {"identifier": "verify@example.com", "code": code})

    assert resp.status_code == 302
    assert resp.url == reverse("family:home")
    assert client.session["_auth_user_id"] == str(account.pk)


def test_verify_code_is_case_insensitive(client):
    account = Account.objects.create_user(email="verify@example.com")
    _token, code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )

    resp = client.post(
        reverse("accounts:verify_code"), {"identifier": "verify@example.com", "code": code.lower()}
    )

    assert resp.status_code == 302
    assert client.session["_auth_user_id"] == str(account.pk)


def test_verify_code_rejects_a_wrong_code(client):
    account = Account.objects.create_user(email="verify@example.com")
    magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )

    resp = client.post(
        reverse("accounts:verify_code"), {"identifier": "verify@example.com", "code": "ZZZZZZ"}
    )

    assert resp.status_code == 400
    assert "_auth_user_id" not in client.session


def test_verify_code_rejects_an_unknown_identifier(client):
    # Same generic error either way - never confirm which identifiers
    # are actually registered.
    resp = client.post(
        reverse("accounts:verify_code"), {"identifier": "nobody@example.com", "code": "AB1234"}
    )

    assert resp.status_code == 400


def test_verify_code_also_burns_the_link_token(client):
    account = Account.objects.create_user(email="verify@example.com")
    token, code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )
    client.post(reverse("accounts:verify_code"), {"identifier": "verify@example.com", "code": code})
    client.logout()

    resp = client.get(reverse("accounts:verify", args=[token]))

    assert resp.status_code == 400


def test_verify_code_redirects_to_the_accounts_own_person_in_the_tree(client):
    account = Account.objects.create_user(email="verify@example.com")
    family = Family.objects.create(name="Test Family")
    FamilyMembership.objects.create(account=account, family=family)
    person = Person.objects.create(family=family, first_name_en="Me", last_name_en="Test", account=account)
    _token, code = magic_links.issue_token(
        account_uuid=str(account.uuid), channel=ChannelEnum.EMAIL, destination=account.email
    )

    resp = client.post(
        reverse("accounts:verify_code"),
        {"identifier": "verify@example.com", "code": code},
        follow=True,
    )

    assert resp.redirect_chain == [
        (reverse("family:home"), 302),
        (reverse("family:family_tree", args=[person.uuid]), 302),
    ]
    assert resp.status_code == 200


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
