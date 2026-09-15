import base64
import mimetypes
from email.utils import formataddr
from functools import cache

import css_inline
from django.conf import settings
from django.contrib.sites.models import Site
from django.contrib.staticfiles.finders import find as find_static
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse

from config.logging_config import logger

DEFAULT_EMAIL_SENDER_NAME = "Family Tree"
DEFAULT_SMS_SENDER_ID = "FamilyTree"


def _site_base_url() -> str:
    """Builds scheme + domain from the Sites framework rather than request.get_host().

    See SITE_DOMAIN in config/settings.py for how that's seeded - used
    instead of request.get_host() since rendering always happens from a
    Celery task with no request in play. Shared by absolute_url below,
    and formerly by absolute_static_url too - see static_data_uri for
    why the event-type icons no longer need an absolute URL at all.
    """
    domain = Site.objects.get_current().domain
    scheme = "https" if settings.SITE_USE_HTTPS else "http"
    return f"{scheme}://{domain}"


@cache
def static_data_uri(path: str) -> str:
    """Base64-embeds a static asset directly into an HTML email as a data URI.

    Used for the event-type icons (see
    notifications/templates/notifications/email/) instead of a hosted
    `<img src>` URL - no request back to this app's own domain is needed
    for the icon to render, so email rendering has no dependency on
    WhiteNoise/`SITE_DOMAIN` being reachable from wherever the
    recipient's client is. Deliberately not SVG despite these being
    simple icons - real client support for inline `<svg>` markup sits
    around 40% (breaks in classic Outlook Windows, Thunderbird, Samsung
    Mail), while a base64 PNG data URI is close to 81%, with PNG
    specifically called out as the most broadly-compatible embedded
    format (see caniemail.com's image-base64/html-svg feature pages).

    Memoized since a worker process re-sends the same handful of icons
    for the lifetime of every email it ever handles - each one is read
    off disk and base64-encoded exactly once per process, not once per
    send. `finders.find()` (not a URL/`STATIC_ROOT` lookup) is what
    makes this work identically in dev and in the `collectstatic`'d
    production image - it resolves an app-relative static path straight
    to the source file's real path on disk, no server involved.
    """
    file_path = find_static(path)
    if file_path is None:
        raise FileNotFoundError(f"Static asset not found: {path}")
    with open(file_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return f"data:{mime_type};base64,{encoded}"


def absolute_url(view_name: str, *args, **kwargs) -> str:
    """Builds an absolute URL to a page on this site.

    E.g. My Notifications, linked from the email footer - this one has
    to stay a real clickable URL, unlike the icons above.
    """
    return f"{_site_base_url()}{reverse(view_name, args=args, kwargs=kwargs)}"


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
    sent_count = message.send(fail_silently=False)
    logger.info("email sent", to=to, subject=subject)
    return {"sent_count": sent_count}


def send_sms(to: str, body: str, *, sender_id: str = "") -> dict:
    """Pluggable SMS send - defaults to logging only.

    Wire in a real provider (Twilio, AWS SNS, ...) by implementing it
    here and pointing settings.SMS_BACKEND at it.

    Args:
        to: Recipient phone number.
        body: Message text.
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
    backend = settings.SMS_BACKEND
    if backend == "console":
        logger.info("sms logged", to=to, body=body, sender_id=sender_id)
        return {"status": "logged", "to": to, "sender_id": sender_id}
    raise NotImplementedError(f"SMS backend '{backend}' is not implemented yet.")
