from urllib.parse import quote

from django import template

from notifications import services

register = template.Library()


@register.simple_tag(name="event_icon_url", takes_context=True)
def event_icon_url_tag(context: dict, path: str) -> str:
    return services.static_absolute_url(path, base_url=context.get("base_url"))


@register.simple_tag(takes_context=True)
def absolute_page_url(context: dict, view_name: str) -> str:
    return services.absolute_url(view_name=view_name, base_url=context.get("base_url"))


@register.simple_tag(takes_context=True)
def manage_settings_url(context: dict) -> str:
    """The "Manage notification settings" footer link, identifier and all.

    See services.IDENTIFIER_PLACEHOLDER's own docstring for why this is a
    placeholder rather than the real recipient - notifications.tasks
    swaps it in per Message right before send. Letting the sign-in page
    prefill from it (accounts.views.RequestMagicLinkView) is what makes
    the placeholder worth carrying at all: a family member who isn't
    signed in on this browser and clicks this link doesn't have to go
    dig up which email/phone they're registered under.

    takes_context=True so this (and event_icon_url/absolute_page_url
    above) can honor a family's own Family.resolved_base_url when the
    caller's render context carries one as "base_url" - see
    services.absolute_url's own docstring. Reading it from context
    rather than taking it as a tag argument means none of the ~15 per-
    event-type email templates that call these tags need to change;
    only the handful of places that build the render context do.
    """
    url = services.absolute_url("notifications:subscriptions", base_url=context.get("base_url"))
    return f"{url}?identifier={quote(services.IDENTIFIER_PLACEHOLDER)}"
