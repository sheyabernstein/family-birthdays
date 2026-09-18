from django.contrib import admin
from reversion.admin import VersionAdmin

from tenants.models import Family, FamilyMembership


@admin.register(Family)
class FamilyAdmin(VersionAdmin):
    list_display = ("name", "slug", "sms_sender_id", "reply_to_email", "created_at")
    search_fields = ("name", "slug")


@admin.register(FamilyMembership)
class FamilyMembershipAdmin(VersionAdmin):
    list_display = ("account", "family", "role", "joined_at")
    list_select_related = ("account", "family")
    list_filter = ("role", "family")
    autocomplete_fields = ("account", "family")
