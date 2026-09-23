import pytest
from django.conf import settings
from django.template import Context, Template

from notifications.services import IDENTIFIER_PLACEHOLDER
from notifications.templatetags.notifications_extras import (
    absolute_page_url,
    event_icon_url_tag,
    manage_settings_url,
)

pytestmark = pytest.mark.django_db


def test_event_icon_url_delegates_to_the_underlying_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "notifications.templatetags.notifications_extras.services.static_absolute_url",
        lambda path: calls.append(path) or "mocked-icon-url",
    )

    result = event_icon_url_tag(path="notifications/img/event-icons/birth.png")

    assert result == "mocked-icon-url"
    assert calls == ["notifications/img/event-icons/birth.png"]


def test_absolute_page_url_delegates_to_the_underlying_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "notifications.templatetags.notifications_extras.services.absolute_url",
        lambda view_name: calls.append(view_name) or "mocked-page-url",
    )

    result = absolute_page_url(view_name="notifications:subscriptions")

    assert result == "mocked-page-url"
    assert calls == ["notifications:subscriptions"]


def test_event_icon_url_is_registered_and_loadable_from_a_template():
    template = Template(
        "{% load notifications_extras %}{% event_icon_url 'notifications/img/event-icons/birth.png' %}"
    )

    rendered = template.render(Context({}))

    assert rendered.startswith(f"{settings.SITE_BASE_URL}/static/")


def test_absolute_page_url_is_registered_and_loadable_from_a_template():
    template = Template(
        "{% load notifications_extras %}{% absolute_page_url 'notifications:subscriptions' %}"
    )

    rendered = template.render(Context({}))

    assert rendered  # real URL-construction correctness is test_rendering.py's job


def test_manage_settings_url_points_at_subscriptions_with_the_placeholder_identifier():
    result = manage_settings_url()

    assert result.endswith(f"/notifications/?identifier={IDENTIFIER_PLACEHOLDER}")


def test_manage_settings_url_is_registered_and_loadable_from_a_template():
    template = Template("{% load notifications_extras %}{% manage_settings_url %}")

    rendered = template.render(Context({}))

    assert IDENTIFIER_PLACEHOLDER in rendered
