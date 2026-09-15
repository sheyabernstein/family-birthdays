from django import template

from notifications import services

register = template.Library()


@register.simple_tag(name="static_data_uri")
def static_data_uri_tag(path: str) -> str:
    return services.static_data_uri(path)


@register.simple_tag
def absolute_page_url(view_name: str) -> str:
    return services.absolute_url(view_name=view_name)
