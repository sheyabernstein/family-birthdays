from django.contrib import messages
from django.contrib.auth import login, logout
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


class RequestMagicLinkView(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, "accounts/request_link.html")

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

                # channel.name.lower() ("email"/"sms"), not channel itself
                # (ChannelEnum's own friendly "Email"/"SMS" value) - this
                # token can sit in Redis, live in a clicked URL, for up
                # to TOKEN_TTL_SECONDS, so what's stored here shouldn't be
                # coupled to display text that could change. See
                # ChannelEnum's own docstring.
                token = magic_links.issue_token(
                    account_uuid=str(account.uuid), channel=channel.name.lower(), destination=destination
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
                            "ttl_minutes": ttl_minutes,
                            "family_name": family.name if family else None,
                        },
                    )
                    subject, body = "Your sign-in link", html_to_plain_text(html)
                else:
                    html = ""
                    subject, body = "", f"Your sign-in link (valid {ttl_minutes} min): {url}"

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


class VerifyMagicLinkView(View):
    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        payload = magic_links.consume_token(token)

        if not payload:
            if request.user.is_authenticated:
                # Already signed in and the link's spent - a double
                # click, or a mail client prefetching the link before the
                # person themselves clicks it, not a real problem for an
                # already-authenticated session.
                return redirect("family:dashboard")
            # Worth an operator's attention - either an expired/reused
            # link or someone probing the verify endpoint.
            logger.warning("magic link verify failed - invalid or expired token")
            return render(request, "accounts/link_invalid.html", status=400)

        account = Account.objects.filter(uuid=payload["account_uuid"], is_active=True).first()
        if not account:
            logger.warning(
                "magic link verify failed - no matching active account",
                account=payload["account_uuid"],
            )
            return render(request, "accounts/link_invalid.html", status=400)

        # Consuming the token above always burns it, even when the
        # current session is already signed in as someone else - a still-
        # live token must never survive a visit just because this browser
        # happened to be authenticated already. Only skip the actual
        # login() call when it'd be a same-account no-op.
        if request.user.pk != account.pk:
            account.backend = "django.contrib.auth.backends.ModelBackend"
            login(request, account)
            logger.info("account logged in", account=account.uuid)
        return redirect("family:dashboard")


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
