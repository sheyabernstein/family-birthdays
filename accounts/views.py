from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View

from accounts import magic_links
from accounts.forms import AccountContactForm
from accounts.helpers import validate_email_address
from accounts.models import Account
from config.logging_config import logger
from notifications.helpers import html_to_plain_text
from notifications.services import send_email, send_sms
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

        if account and account.is_active:
            if not magic_links.is_rate_limited(account.pk):
                is_email = "@" in identifier
                channel = Account.Channel.EMAIL if is_email else Account.Channel.SMS
                destination = account.email if is_email else account.phone
                family = _sole_family(account)

                token = magic_links.issue_token(
                    account_id=account.pk, channel=channel, destination=destination
                )
                url = request.build_absolute_uri(reverse("accounts:verify", args=[token]))
                ttl_minutes = magic_links.TOKEN_TTL_SECONDS // 60

                if channel == Account.Channel.EMAIL:
                    html = render_to_string(
                        "accounts/email/sign_in.html",
                        {
                            "sign_in_url": url,
                            "ttl_minutes": ttl_minutes,
                            "family_name": family.name if family else None,
                        },
                    )
                    send_email(
                        to=destination,
                        subject="Your sign-in link",
                        body=html_to_plain_text(html),
                        html=html,
                        from_name=family.name if family else "",
                        from_email=family.sender_email if family else "",
                        reply_to=family.reply_to_email if family else "",
                    )
                else:
                    send_sms(
                        to=destination,
                        body=f"Your sign-in link (valid {ttl_minutes} min): {url}",
                        sender_id=family.sms_sender_id if family else "",
                    )

                logger.info("magic link issued", account_id=account.pk, channel=channel)
            else:
                logger.warning("magic link rate limited", account_id=account.pk)

        # Same response whether or not the identifier matched a real
        # account - don't leak which emails/phones are registered.
        return render(request, "accounts/link_sent.html")


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

        account = Account.objects.filter(pk=payload["account_id"], is_active=True).first()
        if not account:
            logger.warning(
                "magic link verify failed - no matching active account",
                account_id=payload["account_id"],
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
            logger.info("account logged in", account_id=account.pk)
        return redirect("family:dashboard")


class LogoutView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        logger.info("account logged out", account_id=request.user.pk)
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
