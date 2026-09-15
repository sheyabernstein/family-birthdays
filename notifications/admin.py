from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from reversion.admin import VersionAdmin

from notifications.models import Broadcast, EventType, Message, NotificationPreference, Occurrence


@admin.register(EventType)
class EventTypeAdmin(VersionAdmin):
    list_display = (
        "name",
        "code",
        "anchor",
        "family",
        "applies_to_union",
        "default_opt_in",
        "recurs",
        "notify_days_before",
    )
    list_filter = ("family",)
    autocomplete_fields = ("family",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[EventType]:
        return super().get_queryset(request=request).select_related("family")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(VersionAdmin):
    list_display = ("account", "event_type", "state", "person", "union", "channel", "created_at")
    list_filter = ("event_type", "state", "channel")
    autocomplete_fields = ("account", "person", "union")


@admin.register(Occurrence)
class OccurrenceAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "hebrew_year",
        "occurrence_date",
        "send_date",
        "shifted_for_shabbat_or_yomtov",
        "is_sent",
    )
    list_filter = ("event_type", "is_sent", "shifted_for_shabbat_or_yomtov")
    date_hierarchy = "send_date"


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("destination", "channel", "subject", "status", "tries", "created_at")
    list_filter = ("channel", "status")
    readonly_fields = [f.name for f in Message._meta.fields]


@admin.register(Broadcast)
class BroadcastAdmin(VersionAdmin):
    list_display = ("family", "text", "created_by", "send_at", "is_sent", "sent_at")
    list_filter = ("family", "is_sent")
    autocomplete_fields = ("created_by", "people")
    date_hierarchy = "send_at"
