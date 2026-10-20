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
        "default_state",
        "always_schedule",
        "recurs",
        "notify_days_before",
    )
    list_select_related = ("family",)
    # RelatedOnlyFieldListFilter - only families with an event type at
    # all, not every family in the system (most event types are the
    # global defaults, family=None, anyway).
    list_filter = (("family", admin.RelatedOnlyFieldListFilter),)
    search_fields = ("name", "code")
    autocomplete_fields = ("family",)


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(VersionAdmin):
    list_display = ("account", "event_type", "state", "person", "union", "channel", "created_at")
    list_select_related = ("account", "event_type", "person", "union")
    list_filter = ("event_type", "state", "channel")
    autocomplete_fields = ("account", "event_type", "person", "union")

    def get_queryset(self, request: HttpRequest) -> QuerySet[NotificationPreference]:
        # str(account) (what the "account" column renders) falls back to
        # Account.display_name -> linked_person, a reverse-FK lookup
        # select_related can't reach - see AccountAdmin's own get_queryset.
        return super().get_queryset(request).prefetch_related("account__people")


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
    # __str__ touches person, union, and event_type - see Occurrence.__str__.
    list_select_related = ("person", "union", "event_type")
    # shifted_for_shabbat_or_yomtov is a plain @property now (derived from
    # shift_reasons, a JSONField) - list_display still shows it fine (as
    # plain True/False text rather than the checkmark icon a real
    # BooleanField gets), but list_filter needs an actual queryable
    # field/a custom SimpleListFilter, which a property/JSONField can't
    # provide out of the box - dropped rather than faked.
    list_filter = ("event_type", "is_sent")
    search_fields = (
        "person__first_name_en",
        "person__last_name_en",
        "union__person_a__first_name_en",
        "union__person_a__last_name_en",
        "union__person_b__first_name_en",
        "union__person_b__last_name_en",
    )
    autocomplete_fields = ("person", "union", "event_type")
    date_hierarchy = "send_date"


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("destination", "channel", "subject", "status", "tries", "created_at")
    list_filter = ("channel", "status")
    search_fields = (
        "destination",
        "occurrence__person__first_name_en",
        "occurrence__person__last_name_en",
    )
    readonly_fields = [f.name for f in Message._meta.fields]


@admin.register(Broadcast)
class BroadcastAdmin(VersionAdmin):
    list_display = ("family", "text", "created_by", "send_at", "is_sent", "sent_at")
    list_select_related = ("family", "created_by")
    # RelatedOnlyFieldListFilter - only families with a broadcast at all,
    # not every family in the system.
    list_filter = (("family", admin.RelatedOnlyFieldListFilter), "is_sent")
    search_fields = ("text",)
    autocomplete_fields = ("created_by", "family", "people")
    date_hierarchy = "send_at"

    def get_queryset(self, request: HttpRequest) -> QuerySet[Broadcast]:
        # str(created_by) (what the "created_by" column renders) falls
        # back to Account.display_name -> linked_person, a reverse-FK
        # lookup select_related can't reach - see AccountAdmin's own
        # get_queryset.
        return super().get_queryset(request).prefetch_related("created_by__people")
