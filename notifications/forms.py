from django import forms
from django.db.models import Q

from family.models import Person
from family.widgets import PersonMultiPickerSelect, _person_option_label
from notifications.models import Broadcast
from notifications.widgets import TrixEditorWidget
from tenants.models import Family


class BroadcastForm(forms.ModelForm):
    """Owner/editor only - see notifications.views.BroadcastCreateView/BroadcastUpdateView.

    created_by isn't a form field - it's set on the instance by the view
    from request.user, the same way PersonForm sets instance.family
    directly rather than exposing it as a field.
    """

    class Meta:
        model = Broadcast
        fields = ["text", "people", "send_at"]
        widgets = {
            "text": TrixEditorWidget(),
            # format= is needed on top of type="datetime-local" - without
            # it Django renders an edited instance's current value using
            # DATETIME_INPUT_FORMATS' default ("Y-m-d H:M:S"), which the
            # browser's datetime-local input silently refuses to prefill
            # from (it needs the "T" separator, no seconds).
            "send_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, family: Family, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.family = family
        if not self.instance.pk:
            self.instance.family = family
        self.fields["people"].widget = PersonMultiPickerSelect()
        # Untracked people (notifications_enabled=False - lineage-only
        # stubs, see AGENTS.md) never get notified of anything, a
        # broadcast included, so there's no point offering them here -
        # unlike the father/mother pickers, where an untracked stub is a
        # completely normal, correct choice. Same as _parent_queryset's
        # own "never drops what's already assigned" rule though: if one
        # was tied to this broadcast before becoming untracked, keep
        # showing them rather than silently dropping them on save.
        tracked_or_already_tied = Q(notifications_enabled=True)
        if self.instance.pk:
            tracked_or_already_tied |= Q(pk__in=self.instance.people.values_list("pk", flat=True))
        self.fields["people"].queryset = Person.objects.filter(family=family).filter(tracked_or_already_tied)
        self.fields["people"].required = False
        self.fields["people"].label_from_instance = _person_option_label
        # The browser submits a datetime-local value with no seconds and a
        # "T" separator (e.g. "2026-01-15T10:30") - not one of Django's
        # default DATETIME_INPUT_FORMATS, which would otherwise reject it.
        self.fields["send_at"].input_formats = ["%Y-%m-%dT%H:%M"]
