from urllib.parse import quote

from django import template

from notifications import services

register = template.Library()


@register.simple_tag(name="event_icon_url")
def event_icon_url_tag(path: str) -> str:
    return services.static_absolute_url(path)


@register.simple_tag
def absolute_page_url(view_name: str) -> str:
    return services.absolute_url(view_name=view_name)


@register.simple_tag
def manage_settings_url() -> str:
    """The "Manage notification settings" footer link, identifier and all.

    See services.IDENTIFIER_PLACEHOLDER's own docstring for why this is a
    placeholder rather than the real recipient - notifications.tasks
    swaps it in per Message right before send. Letting the sign-in page
    prefill from it (accounts.views.RequestMagicLinkView) is what makes
    the placeholder worth carrying at all: a family member who isn't
    signed in on this browser and clicks this link doesn't have to go
    dig up which email/phone they're registered under.
    """
    return f"{services.absolute_url('notifications:subscriptions')}?identifier={quote(services.IDENTIFIER_PLACEHOLDER)}"
