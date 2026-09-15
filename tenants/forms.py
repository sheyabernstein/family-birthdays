from django import forms

from tenants.models import Family


class FamilySenderSettingsForm(forms.ModelForm):
    """Owner/editor only - see tenants.views.FamilySettingsView.

    Both fields are optional (sms_sender_id falls back to notifications.
    services.DEFAULT_SMS_SENDER_ID when blank; reply_to_email blank just
    omits the Reply-To header entirely), so there's nothing else to
    validate here beyond what the model fields already enforce
    (sms_sender_id's own alphanumeric/length constraints, reply_to_email's
    own EmailField format check).
    """

    class Meta:
        model = Family
        fields = ["sms_sender_id", "reply_to_email"]
