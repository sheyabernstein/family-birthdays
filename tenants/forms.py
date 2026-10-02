from django import forms

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
