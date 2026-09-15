import reversion
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class AccountManager(BaseUserManager):
    use_in_migrations = True

    def _create(
        self,
        *,
        email: str | None = None,
        phone: str | None = None,
        password: str | None = None,
        **extra_fields,
    ) -> "Account":
        if not email and not phone:
            raise ValueError("An account needs an email or a phone number.")
        email = self.normalize_email(email) if email else None
        account = self.model(email=email, phone=phone, **extra_fields)
        if password:
            account.set_password(password)
        else:
            account.set_unusable_password()
        account.save(using=self._db)
        return account

    def create_user(self, *, email: str | None = None, phone: str | None = None, **extra_fields) -> "Account":
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create(email=email, phone=phone, **extra_fields)

    def create_superuser(
        self,
        *,
        email: str | None = None,
        phone: str | None = None,
        password: str | None = None,
        **extra_fields,
    ) -> "Account":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not email:
            raise ValueError("Superusers need an email (used for admin login).")
        return self._create(email=email, phone=phone, password=password, **extra_fields)


@reversion.register()
class Account(AbstractBaseUser, PermissionsMixin):
    """A login identity.

    Not every family member needs one, and not every Account is
    necessarily linked to a Person (e.g. an in-law managing notifications
    on behalf of someone who isn't set up themselves).

    No passwords: sign-in is exclusively via magic link, sent to whichever
    of email/phone is on file for the account.
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(
        max_length=32, unique=True, null=True, blank=True, help_text="E.164 format, e.g. +15551234567"
    )
    display_name = models.CharField(max_length=255, blank=True)
    preferred_channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.EMAIL)

    # Top of the notification preference hierarchy: everyone is subscribed
    # to everything by default (see notifications.audience), and these are
    # the account-wide kill switches. Below this, notifications.
    # NotificationPreference narrows things further - a whole event type
    # (including to just the immediate family), or one person/union's
    # event specifically.
    email_notifications_enabled = models.BooleanField(default=True)
    sms_notifications_enabled = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = AccountManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(email__isnull=False) | models.Q(phone__isnull=False),
                name="account_has_email_or_phone",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name or self.email or self.phone or f"Account {self.pk}"

    @classmethod
    def find_by_identifier(cls, identifier: str) -> "Account | None":
        """Look an account up by email or phone for the magic-link request flow.

        Independent of USERNAME_FIELD, since phone-only accounts need to
        be findable too.
        """
        identifier = identifier.strip()
        if "@" in identifier:
            return cls.objects.filter(email__iexact=identifier).first()
        return cls.objects.filter(phone=identifier).first()
