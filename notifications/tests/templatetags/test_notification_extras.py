import pytest
from django.template import Context, Template

from notifications.templatetags.notifications_extras import absolute_page_url, static_data_uri_tag

pytestmark = pytest.mark.django_db


def test_static_data_uri_delegates_to_the_underlying_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "notifications.templatetags.notifications_extras.services.static_data_uri",
        lambda path: calls.append(path) or "mocked-data-uri",
    )

    result = static_data_uri_tag(path="notifications/img/event-icons/birth.png")

    assert result == "mocked-data-uri"
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


def test_static_data_uri_is_registered_and_loadable_from_a_template():
    template = Template(
        "{% load notifications_extras %}{% static_data_uri 'notifications/img/event-icons/birth.png' %}"
    )

    rendered = template.render(Context({}))

    assert rendered.startswith("data:image/png;base64,")


def test_absolute_page_url_is_registered_and_loadable_from_a_template():
    template = Template(
        "{% load notifications_extras %}{% absolute_page_url 'notifications:subscriptions' %}"
    )

    rendered = template.render(Context({}))

    assert rendered  # real URL-construction correctness is test_rendering.py's job
