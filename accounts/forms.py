from typing import Any

from django import forms

from accounts.models import Account


class AccountContactForm(forms.ModelForm):
    """Self-service editing of your own contact info and channel toggles, from My Notifications.

    Distinct from family.forms.PersonForm's contact fields, which are an
    owner/editor entering this on someone else's behalf (e.g. a family
    member who doesn't use the app themselves) and have to
    find-or-create-and-link an Account to do it. Here the Account is
    already `request.user` - there's no linking decision to make, just
    editing your own record, so the email/phone uniqueness PersonForm has
    to check for explicitly falls out of Account.email/phone's own
    `unique=True` via normal ModelForm validation.
    """

    class Meta:
        model = Account
        fields = ["email", "phone", "email_notifications_enabled", "sms_notifications_enabled"]

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        # Mirrors Account's own account_has_email_or_phone constraint -
        # better to reject this here with a real form error than let it
        # reach the database as a raw IntegrityError.
        if not cleaned_data.get("email") and not cleaned_data.get("phone"):
            raise forms.ValidationError("An account needs an email or a phone number.")
        return cleaned_data
