from django import template

from notifications import services

register = template.Library()


@register.simple_tag(name="event_icon_url")
def event_icon_url_tag(path: str) -> str:
    return services.static_absolute_url(path)


@register.simple_tag
def absolute_page_url(view_name: str) -> str:
    return services.absolute_url(view_name=view_name)
