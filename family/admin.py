from django.contrib import admin
from reversion.admin import VersionAdmin

from family.models import Person, Union


@admin.register(Person)
class PersonAdmin(VersionAdmin):
    list_display = (
        "display_name",
        "first_name_en",
        "last_name_en",
        "is_living",
        "dob_gregorian",
        "dod_gregorian",
    )
    search_fields = ("first_name_en", "last_name_en", "first_name_he", "last_name_he", "nickname")
    list_filter = ("is_living", "gender")
    autocomplete_fields = ("father", "mother")


@admin.register(Union)
class UnionAdmin(VersionAdmin):
    list_display = ("__str__", "status", "marriage_date_gregorian")
    list_select_related = ("person_a", "person_b")
    search_fields = (
        "person_a__first_name_en",
        "person_a__last_name_en",
        "person_b__first_name_en",
        "person_b__last_name_en",
    )
    autocomplete_fields = ("person_a", "person_b")
