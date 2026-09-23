from email.utils import formataddr
from functools import lru_cache

import css_inline
import phonenumbers
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.templatetags.static import static
from django.urls import reverse
from django.utils.module_loading import import_string

from config.logging_config import logger
from config.observability import metrics
from notifications.sms import SmsBackend

DEFAULT_EMAIL_SENDER_NAME = "Family Tree"
DEFAULT_SMS_SENDER_ID = "FamilyTree"

# A stand-in for the actual recipient's email/phone in the "Manage
# notification settings" footer link (templates/email/_base.html) - the
# link is baked into html_body once per channel, shared by every
# recipient on that send (see notifications.tasks.send_due_notifications'
# own comment on why), so the real destination can't be known yet at
# render time. notifications.tasks swaps this back out for the real
# Message.destination with a plain string .replace() right before each
# recipient's own Message row is created - cheap, and keeps the
# once-per-channel template render itself genuinely shared.
IDENTIFIER_PLACEHOLDER = "__RECIPIENT_IDENTIFIER__"


def static_absolute_url(path: str) -> str:
    """Builds an absolute, stable URL to a static asset, for embedding in email HTML.

    Used for the event-type icons (see
    notifications/templates/notifications/email/) - a real hosted
    `<img src>` rather than a base64 data URI, since Gmail (both webmail
    and its apps) never renders a data URI image at all, while a hosted
    one just falls under Gmail's completely normal "images are blocked
    until you click Display images below" gate, the same as any other
    email with images. Uses `settings.SITE_BASE_URL`, the same source
    `absolute_url()` below uses - never `EMAIL_SENDING_DOMAIN`, which is
    only about the `From:` address and has nothing to do with where a
    static asset is actually served from (see AGENTS.md).

    Relies on `static()` resolving through the "staticfiles" storage's
    own `url()` - `config.storage.StableStaticFilesStorage` deliberately
    leaves this path's filename unhashed for exactly this reason: a
    hashed name would 404 in every already-sent email the moment a
    later `collectstatic` run reassigned the hash, since `Message.
    html_body` is rendered once and persisted, not re-rendered on read.
    """
    return f"{settings.SITE_BASE_URL}{static(path)}"


def absolute_url(view_name: str, *args, **kwargs) -> str:
    """Builds an absolute URL to a page on this site.

    E.g. My Notifications, linked from the email footer, or the sign-in
    magic link (accounts.views.RequestMagicLinkView) - both need a real,
    correct clickable URL with no request in play (a Celery task, or a
    scheme that has to reflect settings.SITE_BASE_URL rather than
    whatever request.is_secure() would say - this app always terminates
    TLS upstream, so that's never a reliable signal, see AGENTS.md).
    """
    return f"{settings.SITE_BASE_URL}{reverse(view_name, args=args, kwargs=kwargs)}"


def _inline_css(html: str) -> str:
    """Inlines an email's CSS for wider mail-client compatibility.

    Rewrites every plain (non-`@media`) rule in `html`'s own `<style>`
    block directly onto each element's own `style="..."` attribute -
    Mailpit's own "HTML Check" tab is what surfaced why this matters:
    plain embedded `<style>` rules are only ~61% fully supported across
    real clients (background specifically ~58%, and a `<body>` background
    is outright unsupported in a third of them), while an inline `style`
    attribute is close to universally honored. `keep_at_rules=True` is
    the one setting that matters here - `@media (prefers-color-scheme:
    dark)` can't be resolved statically at send time (which rule applies
    depends on the recipient's own client), so it has to survive as real
    CSS for a supporting client to still apply; `keep_style_tags=False`
    just means the (now-redundant, since it's all inlined) plain rules
    aren't also left behind duplicated in the head. A light-mode rule
    that leans on `!important` would itself become an inlined
    `!important` here, which - per the CSS spec - beats a same-`!important`
    class-selector rule in the surviving `@media` block purely on
    inline's own higher specificity; found for real via `.btn-email-text`
    in templates/email/_base.html, which no longer needs `!important` at
    all now that a plain class selector already outranks the plain `a`
    rule it was guarding against. Falls back to the un-inlined HTML on a
    genuine parse/CSS error rather than blocking the send over what's
    purely a rendering enhancement.
    """
    try:
        return css_inline.inline(html, keep_at_rules=True, keep_style_tags=False)
    except css_inline.InlineError as exc:
        logger.warning("css inlining failed, sending un-inlined html", exc_info=exc)
        return html


def send_email(
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    *,
    event_type: str,
    from_name: str = "",
    from_email: str = "",
    reply_to: str = "",
) -> dict:
    """Sends a multipart (plain-text + optional HTML) email via EmailMultiAlternatives.

    Built directly on EmailMultiAlternatives rather than the send_mail()
    shortcut, since that shortcut has no way to set Reply-To. See
    notifications.tasks for how `body`/`html` are actually rendered, per
    event type, from a Django template rather than an f-string.
    `formataddr` handles quoting/encoding a name with a comma, quotes, or
    non-ASCII characters correctly, rather than a raw f-string that
    would produce an invalid header for those.

    Args:
        to: Recipient address.
        subject: Email subject line.
        body: The plain-text part every client falls back to if it
            can't (or won't) render HTML.
        html: If given, attached as the real rendered alternative (a
            genuine multipart email, not html-only with a lossy
            afterthought) - run through _inline_css first (see that
            function's own docstring for why).
        event_type: One of notifications.models.EventType.BuiltinCode's
            values, "custom" (a family-defined event type), or
            "magic_link" - labels the
            config.observability.metrics.notifications_emails_sent_total
            counter incremented below. Required, not defaulted, so a new
            call site can't silently go unlabeled.
        from_name: A family's own sender identity - just Family.name
            (see AGENTS.md - there's no separate "email sender name"
            field, the family's own name is the name). Blank - which is
            also what's used for messages with no family context at
            all, e.g. the magic-link sign-in email in accounts.views -
            falls back to DEFAULT_EMAIL_SENDER_NAME.
        from_email: Family.sender_email
            (noreply-{slug}@settings.EMAIL_SENDING_DOMAIN, one shared
            sending domain verified once, not per-family - see that
            property's own docstring). Blank falls back to
            settings.DEFAULT_FROM_EMAIL.
        reply_to: A family's own optional Family.reply_to_email -
            omitted entirely (not defaulted to anything) when blank, so
            a reply just goes nowhere useful, same as it already would
            without this feature.

    Returns:
        A dict with the number of messages actually sent (Django's own
        EmailMultiAlternatives.send() return value).
    """
    from_name = from_name or DEFAULT_EMAIL_SENDER_NAME
    from_email = from_email or settings.DEFAULT_FROM_EMAIL
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=formataddr((from_name, from_email)),
        to=[to],
        reply_to=[reply_to] if reply_to else None,
    )
    if html:
        message.attach_alternative(_inline_css(html), "text/html")
    try:
        sent_count = message.send(fail_silently=False)
    except Exception:
        metrics.notifications_emails_sent_total.labels(status="failed", event_type=event_type).inc()
        raise
    metrics.notifications_emails_sent_total.labels(status="sent", event_type=event_type).inc()
    logger.info("email sent", to=to, subject=subject)
    return {"sent_count": sent_count}


@lru_cache(maxsize=1)
def _sms_backend() -> SmsBackend:
    return import_string(settings.SMS_BACKEND)()


@receiver(setting_changed)
def _clear_sms_backend_cache(*, setting: str, **kwargs) -> None:
    """Lets override_settings(SMS_BACKEND=...) actually take effect in tests."""
    if setting == "SMS_BACKEND":
        _sms_backend.cache_clear()


def _country_for_sms_metric(to: str) -> str:
    """Best-effort ISO alpha-2 country code for `to`, for the notifications_sms_sent_total metric.

    `to` is always E.164 (see Account.phone's own docstring) - parsed with
    no default region since the leading "+" already carries the country.
    Falls back to "unknown" on anything unparseable rather than raising -
    this is a metric label, not something worth blocking a real send over.
    """
    try:
        return phonenumbers.region_code_for_number(phonenumbers.parse(to, None)) or "unknown"
    except phonenumbers.NumberParseException:
        return "unknown"


def send_sms(to: str, body: str, *, event_type: str, sender_id: str = "") -> dict:
    """Sends via whichever notifications.sms.SmsBackend settings.SMS_BACKEND names.

    Args:
        to: Recipient phone number.
        body: Message text.
        event_type: One of notifications.models.EventType.BuiltinCode's
            values, "custom" (a family-defined event type), or
            "magic_link" - labels the
            config.observability.metrics.notifications_sms_sent_total
            counter incremented below, alongside a country derived from
            `to`. Required, not defaulted, so a new call site can't
            silently go unlabeled.
        sender_id: A family's own alphanumeric sender ID
            (Family.sms_sender_id) - this app serves many families from
            what's normally one shared sending number/short code, so
            without a per-family sender ID an SMS carries no indication
            of which family it's from. Blank (the default, and also
            what's used for messages with no family context at all,
            e.g. the magic-link sign-in text in accounts.views) falls
            back to DEFAULT_SMS_SENDER_ID rather than sending unbranded.

    Returns:
        A dict describing the send outcome - shape depends on the
        active SMS_BACKEND.
    """
    sender_id = sender_id or DEFAULT_SMS_SENDER_ID
    country = _country_for_sms_metric(to)
    try:
        result = _sms_backend().send(to=to, body=body, sender_id=sender_id)
    except Exception:
        metrics.notifications_sms_sent_total.labels(
            status="failed", event_type=event_type, country=country
        ).inc()
        raise
    metrics.notifications_sms_sent_total.labels(status="sent", event_type=event_type, country=country).inc()
    return result
