from urllib.parse import parse_qs, urlsplit

from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.views import View

from accounts import magic_links
from accounts.forms import AccountContactForm
from accounts.helpers import validate_email_address
from accounts.models import Account
from accounts.tasks import send_magic_link_message
from config.logging_config import logger
from notifications.enums import ChannelEnum
from notifications.helpers import html_to_plain_text
from notifications.services import absolute_url
from tenants.mixins import FamilyRequiredMixin
from tenants.models import Family


def _sole_family(account: Account) -> Family | None:
    """The one family to brand a sign-in message with, if there's an unambiguous choice.

    None for zero or (someone in more than one family - see AGENTS.md's
    Person/Account bullet) several, since picking one arbitrarily would
    misrepresent who's actually sending it. Every other email/SMS this
    app sends already has an obvious single family behind it (an
    Occurrence's/Broadcast's own) - a sign-in link is the one message
    type that doesn't, since it's requested before the account's even
    picked a workspace for this session.
    """
    memberships = list(account.family_memberships.select_related("family")[:2])
    return memberships[0].family if len(memberships) == 1 else None


def _prefill_identifier(request: HttpRequest) -> str:
    """The identifier to prefill the sign-in form with, if this visit's URL carries one.

    Never auto-submits anything (see request_link.html) - this only
    saves a re-type, it still takes a real click to actually request a
    link. Checked two ways: a direct ?identifier= on this page's own
    URL, and - since the far more common path here is
    LoginRequiredMixin's own redirect_to_login() (e.g. a signed-out visit
    to the "Manage notification settings" link every notification email
    carries, see templates/email/_base.html's own manage_settings_url
    tag) - nested inside that redirect's own ?next= querystring instead
    of a sibling parameter next to it.
    """
    identifier = request.GET.get("identifier", "")
    if identifier:
        return identifier
    next_url = request.GET.get(REDIRECT_FIELD_NAME, "")
    next_query = urlsplit(next_url).query
    return parse_qs(next_query).get("identifier", [""])[0]


class RequestMagicLinkView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, "accounts/request_link.html", {"identifier": _prefill_identifier(request)})

    def post(self, request: HttpRequest) -> HttpResponse:
        identifier = request.POST.get("identifier", "")
        account = Account.find_by_identifier(identifier)
        # A fixed app-wide constant, not per-request state - safe to show
        # on the confirmation page unconditionally, same as the identifier
        # itself, regardless of whether a link was actually issued below.
        ttl_minutes = magic_links.TOKEN_TTL_SECONDS // 60

        is_email = "@" in identifier
        channel = ChannelEnum.EMAIL if is_email else ChannelEnum.SMS

        if not account or not account.is_active:
            logger.info(
                "ignoring magic link request",
                identifier=identifier,
                account=account.uuid if account else None,
                is_active=account.is_active if account else None,
            )
        else:
            if not magic_links.is_rate_limited(str(account.uuid)):
                destination = account.email if is_email else account.phone
                family = _sole_family(account)

                token, code = magic_links.issue_token(
                    account_uuid=str(account.uuid), channel=channel, destination=destination
                )
                # Not request.build_absolute_uri() - that derives the scheme
                # from request.is_secure(), which is only ever True if
                # Django itself terminates TLS. This app never does (TLS is
                # terminated upstream, by a reverse proxy - see AGENTS.md),
                # so that always evaluated to plain http:// regardless of
                # how the site's actually served - a real bug, not
                # hypothetical. absolute_url() uses SITE_BASE_URL directly
                # instead, the same source of truth every other
                # request-less absolute URL in this app already uses (see
                # notifications.services._site_base_url).
                url = absolute_url("accounts:verify", token)

                if channel == ChannelEnum.EMAIL:
                    html = render_to_string(
                        "accounts/email/sign_in.html",
                        {
                            "sign_in_url": url,
                            "code": code,
                            "ttl_minutes": ttl_minutes,
                            "family_name": family.name if family else None,
                        },
                    )
                    subject, body = "Your sign-in link", html_to_plain_text(html)
                else:
                    html = ""
                    subject, body = "", (
                        f"Your sign-in link (valid {ttl_minutes} min): {url}\n\nOr enter code {code}"
                    )

                # Dispatched, not sent inline - see accounts.tasks.
                # send_magic_link_message's own docstring for why this
                # still always runs at TaskPriority.HIGH regardless of
                # whatever else is queued.
                task = send_magic_link_message.delay(
                    account_uuid=str(account.uuid),
                    channel=channel,
                    destination=destination,
                    subject=subject,
                    body=body,
                    html=html,
                    from_name=family.name if family else "",
                    from_email=family.sender_email if family else "",
                    reply_to=family.reply_to_email if family else "",
                    sms_sender_id=family.sms_sender_id if family else "",
                )

                logger.info(
                    "magic link issued",
                    account=account.uuid,
                    identifier=identifier,
                    channel=channel,
                    task_id=task.id,
                )
            else:
                logger.warning(
                    "magic link rate limited", account=account.uuid, channel=channel, identifier=identifier
                )

        # Same response whether or not the identifier matched a real
        # account - don't leak which emails/phones are registered.
        # Echoing the identifier back is safe regardless - it's just what
        # they themselves typed a moment ago, not anything about whether
        # it matched a real account.
        return render(
            request, "accounts/link_sent.html", {"identifier": identifier, "ttl_minutes": ttl_minutes}
        )


def _log_in_and_redirect(
    request: HttpRequest, account: Account, *, via: str, identifier: str
) -> HttpResponse:
    """Shared by VerifyMagicLinkView and VerifyCodeView - the code is just a second pointer at the same token.

    Only skips the actual login() call when it'd be a same-account
    no-op - the caller's own token/code was already burned by the time
    this runs regardless (see magic_links.consume_token/consume_code), so
    a still-live one must never survive a visit just because this
    browser happened to already be authenticated. `via` ("link" or
    "code") and `identifier` (the token/code payload's own
    `destination` - whichever of the account's email/phone this
    particular sign-in actually went out to, not just whichever field
    happens to be set) are logged alongside the sign-in so the two paths
    stay distinguishable in a log line the callers otherwise share
    verbatim.
    """
    if request.user.pk != account.pk:
        account.backend = "django.contrib.auth.backends.ModelBackend"
        login(request, account)
        logger.info("account logged in", account=account.uuid, via=via, identifier=identifier)
    # Redirects to family:home rather than resolving the landing page
    # here directly - CurrentFamilyMiddleware already ran for *this*
    # request before login() was called, off of whatever request.user
    # was at the start of the request (anonymous, on a real sign-in), so
    # request.family/request.self_person here would still reflect the
    # pre-login state. family:home (family.views.HomeView) does the
    # actual self-person-or-Upcoming resolution on the browser's
    # follow-up request, once middleware has run again for the
    # now-authenticated session.
    return redirect("family:home")


def _invalid_link_response(request: HttpRequest, token: str) -> HttpResponse:
    """The shared "that didn't work" response for both GET (peek) and POST (consume) failures.

    Distinguishes a tombstoned (already-consumed) token from a
    plain bad/expired one - see magic_links.is_token_spent's own
    docstring for why that's knowable at all - so the page can nudge
    someone whose link an email scanner beat them to towards a resend
    or the code field, instead of a bare dead end.
    """
    spent = magic_links.is_token_spent(token)
    logger.warning("magic link verify failed", spent=spent, via="link")
    return render(request, "accounts/link_invalid.html", {"spent": spent}, status=400)


class VerifyMagicLinkView(View):
    """GET never spends the token - only the confirm button's POST does.

    An email security scanner or a link-preview (iOS long-press "Peek",
    Outlook Safe Links, corporate secure-email gateways) fetches the
    link's URL - a real GET request - before the person themselves ever
    sees it. If that GET consumed the token outright (as it used to),
    the scanner silently burns it and the real click lands on a dead
    "invalid link" page with no clue why. None of those scanners click a
    button, so gating consumption behind one defeats them without any
    heuristics about who's asking.
    """

    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        if request.user.is_authenticated:
            # Already signed in - a double click, or exactly the
            # prefetch case above, either way not worth a confirm
            # screen for a session that already exists.
            return redirect("family:home")

        payload = magic_links.peek_token(token)
        if not payload:
            return _invalid_link_response(request, token)

        return render(request, "accounts/link_confirm.html", {"token": token})

    def post(self, request: HttpRequest, token: str) -> HttpResponse:
        payload = magic_links.consume_token(token)
        if not payload:
            return _invalid_link_response(request, token)

        account = Account.objects.filter(uuid=payload["account_uuid"], is_active=True).first()
        if not account:
            logger.warning(
                "magic link verify failed - no matching active account",
                account=payload["account_uuid"],
                via="link",
            )
            return render(request, "accounts/link_invalid.html", status=400)

        return _log_in_and_redirect(request, account, via="link", identifier=payload["destination"])


class VerifyCodeView(View):
    """The short-code alternative to clicking the magic link - see accounts/magic_links.py's own docstrings.

    POST-only - the form lives inline on link_sent.html itself (the
    identifier is already known from that page's own context, tucked
    into a hidden field), not a separately-navigable page of its own. A
    failed attempt re-renders that same template rather than a dedicated
    one.
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        identifier = request.POST.get("identifier", "").strip()
        code = request.POST.get("code", "").strip()
        account = Account.find_by_identifier(identifier)

        payload = None
        if account and account.is_active:
            payload = magic_links.consume_code(account_uuid=str(account.uuid), code=code)

        if not payload:
            # One combined error covers a wrong code, an unknown
            # identifier, and a locked-out account alike - same
            # don't-leak-which-identifiers-are-registered reasoning as
            # RequestMagicLinkView.post's own shared response.
            logger.warning("magic code verify failed", identifier=identifier, via="code")
            return render(
                request,
                "accounts/link_sent.html",
                {"identifier": identifier, "code_error": True},
                status=400,
            )

        return _log_in_and_redirect(request, account, via="code", identifier=payload["destination"])


class LogoutView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        logger.info("account logged out", account=request.user.uuid)
        logout(request)
        return redirect("accounts:request_link")


class ValidateEmailView(LoginRequiredMixin, View):
    """AJAX-only assistive validation for an email field mid-entry.

    Checks format (Django's own EmailValidator), a domain/TLD typo
    suggestion, and a real MX/A-record check - see accounts.helpers.
    validate_email_address's own docstring for why each of those.
    Login-required only, like GregorianToHebrewView - this doesn't touch
    family data. Never authoritative: PersonForm/AccountContactForm still
    validate for real at save time regardless of what this returns.
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> JsonResponse:
        address = request.POST.get("address", "").strip()
        if not address:
            return JsonResponse({"error": "Missing address."}, status=400)

        result = validate_email_address(address)
        return JsonResponse(
            {
                "valid": result.valid,
                "message": result.message,
                "suggestions": result.suggestions,
            }
        )


class UpdateAccountSettingsView(FamilyRequiredMixin, View):
    """The Channels card on My Notifications.

    Your own email/phone and the top-level per-channel on/off switch, all
    in one form. Editing contact info for someone *else* still goes
    through family.forms.PersonForm (owner/editor only) - this is only
    ever request.user's own Account.
    """

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        form = AccountContactForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Updated your notification settings.")
        else:
            # Field errors (e.g. "Account with this Email already exists")
            # matter here as much as the form-wide one from clean() -
            # flatten both rather than only surfacing __all__.
            errors = [message for field_errors in form.errors.values() for message in field_errors]
            messages.error(request, " ".join(errors) or "Couldn't save your changes.")
        return redirect("notifications:subscriptions")
