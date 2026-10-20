from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from reversion.admin import VersionAdmin

from tenants.models import Family, FamilyMembership


@admin.register(Family)
class FamilyAdmin(VersionAdmin):
    autocomplete_fields = ("tree_root",)
    list_display = (
        "name",
        "email_sending_domain",
        "sms_sender_id",
        "reply_to_email",
        "email_sending_enabled",
        "sms_sending_enabled",
        "created_at",
    )
    search_fields = ("name", "slug")


@admin.register(FamilyMembership)
class FamilyMembershipAdmin(VersionAdmin):
    list_display = ("account", "family", "role", "joined_at")
    list_select_related = ("account", "family")
    list_filter = ("role", "family")
    autocomplete_fields = ("account", "family")

    def get_queryset(self, request: HttpRequest) -> QuerySet[FamilyMembership]:
        # account itself is already select_related above, but str(account)
        # (what the "account" column actually renders) falls back to
        # Account.display_name -> linked_person, a reverse-FK lookup
        # select_related can't reach - see AccountAdmin's own get_queryset.
        return super().get_queryset(request).prefetch_related("account__people")
