from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html
from reversion.admin import VersionAdmin

from accounts.models import Account


@admin.register(Account)
class AccountAdmin(VersionAdmin):
    # No explicit ordering - Account.Meta.ordering already covers this.
    list_display = ("email", "phone", "linked_person_link", "preferred_channel", "is_staff", "is_active")
    search_fields = ("email", "phone")
    exclude = ("password",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Account]:
        return super().get_queryset(request).prefetch_related("people")

    @admin.display(description="Linked person")
    def linked_person_link(self, account: Account) -> str:
        person = account.linked_person
        if person is None:
            return "—"
        url = reverse("admin:family_person_change", args=[person.pk])
        return format_html('<a href="{}">{}</a>', url, person.display_name)
