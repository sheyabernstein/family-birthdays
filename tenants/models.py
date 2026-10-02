import uuid

import reversion
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils.text import slugify

from accounts.models import Account


@reversion.register()
class Family(models.Model):
    """One extended family's ledger - the tenant boundary.

    Person, Union (via its two Persons), and EventType are all scoped to
    a Family; an Account can belong to more than one (e.g. your own
    family and the one you married into) via FamilyMembership.
    """

    # Identifies this record over HTTP (e.g. the family-switcher form) -
    # never the integer pk. See notifications.models.EventType.uuid, the
    # original instance of this convention.
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True)
    # Shown as the SMS "From" for every text sent on this family's behalf
    # (see notifications.services.send_sms) - this app serves many families
    # from what's normally one shared sending number, so without this, an
    # SMS carries no indication of which family it's from, and someone in
    # more than one family (see the class docstring) couldn't tell them
    # apart at all. Blank falls back to notifications.services.
    # DEFAULT_SMS_SENDER_ID rather than a family-less-but-still-branded text.
    sms_sender_id = models.CharField(
        max_length=10,
        blank=True,
        validators=[RegexValidator(r"^[A-Za-z0-9]*$", "Letters and digits only, no spaces or symbols.")],
        verbose_name="SMS sender ID",
        help_text='Shown as the SMS sender (e.g. "RokachFam") - letters and digits only, up to 10 '
        'characters. Leave blank to use the default ("FamilyTree").',
    )
    # Optional Reply-To for this family's emails - e.g. so a reply to a
    # broadcast reaches an actual person instead of the noreply-* address
    # (see sender_email below) every family's mail otherwise sends from.
    # Blank omits the header entirely, not a fallback address - a reply
    # then just goes nowhere useful, same as it already would without
    # this field.
    reply_to_email = models.EmailField(
        blank=True,
        verbose_name="Reply-To email",
        help_text="Optional - replies to this family's emails go here if set.",
    )
    # A site-admin-only kill switch, not exposed on the self-service
    # Workspace Settings form (tenants.forms.FamilySenderSettingsForm
    # deliberately doesn't list these in Meta.fields) - an owner/editor
    # can see whether sending is off (see family_settings.html) but only
    # a site admin, via Django admin, can change it. Defaults to False
    # for every family, including ones that already existed when this
    # shipped - a brand-new or freshly-admin-onboarded family starts
    # silent rather than accidentally live, same reasoning as there
    # being no self-service family creation at all (see AGENTS.md).
    # Checked as early as possible - notifications.audience.
    # available_channels() requires this family to resolve a channel as
    # a candidate at all, so a disabled channel is never even considered
    # when computing an audience, not merely rejected later at send
    # time (notifications.tasks.send_message needs no matching check,
    # since by the time a Message row exists the channel was already
    # confirmed enabled).
    email_sending_enabled = models.BooleanField(default=False, verbose_name="email sending enabled")
    sms_sending_enabled = models.BooleanField(default=False, verbose_name="SMS sending enabled")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "families"
        ordering = ["name", "pk"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            base = slugify(self.name) or "family"
            slug = base
            suffix = 1
            while Family.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                suffix += 1
                slug = f"{base}-{suffix}"
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def sender_email(self) -> str:
        """This family's own "From" address for email - noreply-{slug}@settings.EMAIL_SENDING_DOMAIN.

        Derived from slug, not a stored field, so it can't drift out of
        sync if the family gets renamed (slug is set once, at creation -
        see save() above). One shared
        sending domain across every family (verified at the domain level
        with SES, not per-address), only the local-part varies - see
        notifications.services.send_email for where the display name
        (just Family.name - see AGENTS.md) gets paired with this.
        """
        return f"noreply-{self.slug}@{settings.EMAIL_SENDING_DOMAIN}"


@reversion.register()
class FamilyMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        EDITOR = "editor", "Editor"
        MEMBER = "member", "Member"

    # Roles that may create/edit people and unions. Deleting is
    # deliberately narrower (owner-only) - see tenants.permissions.
    EDITOR_ROLES = (Role.OWNER, Role.EDITOR)

    # Not currently referenced over HTTP anywhere, but every model gets one
    # regardless (see Family.uuid).
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="family_memberships")
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "family"], name="unique_family_membership"),
        ]
        ordering = ["-joined_at", "pk"]

    def __str__(self) -> str:
        return f"{self.account} in {self.family} ({self.role})"
