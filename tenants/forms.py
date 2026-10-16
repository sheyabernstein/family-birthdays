from django import forms

from family.models import Person
from family.widgets import PersonPickerSelect, _person_option_label, prefetch_for_person_picker
from tenants.models import Family


class FamilySenderSettingsForm(forms.ModelForm):
    """Requires the tenants.change_family permission - see tenants.views.FamilySettingsView.

    Optional (blank just omits the Reply-To header entirely), so there's
    nothing else to validate here beyond reply_to_email's own EmailField
    format check. sms_sender_id/base_url used to live here too, but both
    are admin-only now (see their own docstrings on Family) - this form
    only ever lists what a site-admin-permission-holding owner/editor is
    actually allowed to self-service.
    """

    class Meta:
        model = Family
        fields = ["reply_to_email"]


class FamilyTreeRootForm(forms.ModelForm):
    """Owner role only - see tenants.views.FamilySettingsView.

    A narrower gate than FamilySenderSettingsForm's (the global
    tenants.change_family permission) - this is a structural decision
    about the family's own tree, not site-admin territory.
    """

    class Meta:
        model = Family
        fields = ["tree_root"]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["tree_root"].widget = PersonPickerSelect()
        self.fields["tree_root"].queryset = prefetch_for_person_picker(
            Person.objects.filter(family=self.instance).order_by(*Person._meta.ordering)
        )
        self.fields["tree_root"].label_from_instance = _person_option_label
