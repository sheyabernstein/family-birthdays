from django.contrib import admin
from reversion.admin import VersionAdmin

from accounts.models import Account


@admin.register(Account)
class AccountAdmin(VersionAdmin):
    list_display = ("email", "phone", "display_name", "preferred_channel", "is_staff", "is_active")
    search_fields = ("email", "phone", "display_name")
    ordering = ("email",)
    exclude = ("password",)
