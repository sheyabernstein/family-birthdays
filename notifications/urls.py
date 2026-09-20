from django.urls import path

from accounts.views import UpdateAccountSettingsView
from notifications.views import (
    BroadcastCreateView,
    BroadcastDeleteView,
    BroadcastListView,
    BroadcastUpdateView,
    OccurrencePreviewView,
    ScheduledTasksView,
    SubscriptionsView,
    TogglePersonPreferenceView,
    UpdateEventTypePreferenceView,
)

app_name = "notifications"

urlpatterns = [
    path("admin/scheduled-tasks/", ScheduledTasksView.as_view(), name="scheduled_tasks"),
    path("broadcasts/", BroadcastListView.as_view(), name="broadcast_list"),
    path("broadcasts/new/", BroadcastCreateView.as_view(), name="broadcast_create"),
    path("broadcasts/<uuid:uuid>/edit/", BroadcastUpdateView.as_view(), name="broadcast_update"),
    path("broadcasts/<uuid:uuid>/delete/", BroadcastDeleteView.as_view(), name="broadcast_delete"),
    path("notifications/", SubscriptionsView.as_view(), name="subscriptions"),
    path(
        "notifications/event-type/",
        UpdateEventTypePreferenceView.as_view(),
        name="update_event_type_preference",
    ),
    path("notifications/toggle/", TogglePersonPreferenceView.as_view(), name="toggle_preference"),
    path("occurrences/<uuid:uuid>/preview/", OccurrencePreviewView.as_view(), name="occurrence_preview"),
    # UpdateAccountSettingsView's model (Account) lives in accounts, but the
    # URL stays under this same "notifications/" prefix as the rest of My
    # Notifications - see AGENTS.md.
    path(
        "notifications/settings/",
        UpdateAccountSettingsView.as_view(),
        name="update_notification_settings",
    ),
]
