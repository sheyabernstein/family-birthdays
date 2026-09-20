import uuid

import nh3
import reversion
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from accounts.models import Account
from family.models import Person, Union
from notifications.enums import ChannelEnum
from tenants.models import Family


@reversion.register()
class EventType(models.Model):
    class Anchor(models.TextChoices):
        BIRTH = "birth", "Date of birth"
        DEATH = "death", "Date of death (yahrzeit)"
        MARRIAGE = "marriage", "Marriage date"

    class BuiltinCode(models.TextChoices):
        """The `code`s seeded by notifications/migrations/0001_initial.py for every family.

        Not a `choices=` constraint on the field below, since a family
        can define its own custom event types with any code they like.
        This just gives application code a typo-proof name for the ones
        with special handling, instead of comparing against string
        literals.
        """

        BIRTHDAY = "birthday", "Birthday"
        YAHRZEIT = "yahrzeit", "Yahrzeit"
        ANNIVERSARY = "anniversary", "Anniversary"
        BAR_MITZVAH = "bar_mitzvah", "Bar Mitzvah"
        BAT_MITZVAH = "bat_mitzvah", "Bat Mitzvah"
        WEDDING = "wedding", "Wedding"
        BROADCAST = "broadcast", "Broadcast"

    # Identifies this record over HTTP (form fields, etc.) - never the
    # integer pk, which would let one family guess/enumerate another
    # family's custom event type ids. See Person.uuid for the original
    # instance of this convention.
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    family = models.ForeignKey(
        "tenants.Family",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="event_types",
        help_text="Null means this is a global default available to every family.",
    )
    code = models.SlugField()
    name = models.CharField(max_length=100)
    anchor = models.CharField(
        max_length=10,
        choices=Anchor.choices,
        blank=True,
        help_text="Blank for an event type with no anchor date of its own (e.g. Broadcast, which is "
        "sent immediately/on a schedule rather than computed from a Person/Union date field).",
    )
    applies_to_union = models.BooleanField(
        default=False, help_text="Subject is a Union (e.g. anniversary) rather than a Person"
    )
    default_opt_in = models.BooleanField(
        default=True,
        help_text="Whether people are subscribed to this event type unless they say otherwise. "
        "A family adding a more sensitive custom event type may want it to start opted out instead.",
    )
    recurs = models.BooleanField(
        default=True,
        help_text="Whether this happens every year (birthday, yahrzeit, anniversary) or just once, "
        "in the anchor date's own year (e.g. Wedding - the marriage itself only happens once; the "
        "yearly celebration of it afterwards is the separate Anniversary event type).",
    )
    notify_days_before = models.PositiveSmallIntegerField(
        default=0,
        help_text="How many days ahead of the actual date to send the notification - 0 sends on the "
        "day itself (the default, right for a birthday or yahrzeit). A Wedding reminder, for example, "
        "is more useful sent a few days ahead.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["family", "code"], name="unique_event_type_code_per_family"),
        ]
        ordering = ["name", "pk"]

    def __str__(self) -> str:
        return self.name

    @property
    def badge_class(self) -> str:
        """The `badge-*` CSS class suffix for this event type (see app.css).

        `anchor` for anything anchor-driven (birth/death/marriage share
        one badge color per anchor, not one per event type), falling
        back to `code` for one that isn't anchored at all (currently
        only Broadcast - see AGENTS.md). A blank anchor with no matching
        `.badge-<code>` rule just renders unstyled, the same graceful
        fallback as any other unrecognized badge class.
        """
        return self.anchor or self.code


@reversion.register()
class NotificationPreference(models.Model):
    """Every way an account can depart from a family's default notification opt-in.

    Everyone in a family is notified about everything by default (per
    EventType.default_opt_in - see notifications.audience for the full
    resolution order). This table holds every way an account can depart
    from that default, at two levels of scope:

    - A row with person and union both null applies to the whole event
      type for that account. Its state can mute it entirely, force it
      on (overriding a default_opt_in=False event type), or restrict it
      to the account's immediate family (spouse/parent/child/sibling -
      see notifications.audience.is_immediate_family) for everyone else.
    - A row with person or union set narrows or overrides that down to
      one specific person's or union's event - including forcing one
      person back on despite an immediate-family-only restriction, or
      muting one person despite an otherwise-open subscription.
    """

    class State(models.TextChoices):
        MUTED = "muted", "Muted"
        SUBSCRIBED = "subscribed", "Subscribed"
        IMMEDIATE_FAMILY_ONLY = "immediate_family_only", "Immediate family only"

    # Not currently referenced over HTTP anywhere, but every model gets one
    # regardless (see EventType.uuid) so any future view/URL added against
    # this model defaults to the right identifier without a retrofit.
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="notification_preferences")
    event_type = models.ForeignKey(
        EventType, on_delete=models.CASCADE, related_name="notification_preferences"
    )
    person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.CASCADE, related_name="notification_preferences"
    )
    union = models.ForeignKey(
        Union, null=True, blank=True, on_delete=models.CASCADE, related_name="notification_preferences"
    )
    channel = models.CharField(max_length=10, choices=ChannelEnum.choices())
    state = models.CharField(max_length=25, choices=State.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~(models.Q(person__isnull=False) & models.Q(union__isnull=False)),
                name="preference_not_both_person_and_union",
            ),
            models.CheckConstraint(
                condition=~models.Q(state="immediate_family_only")
                | (models.Q(person__isnull=True) & models.Q(union__isnull=True)),
                name="immediate_family_only_is_whole_type_only",
            ),
            models.UniqueConstraint(
                fields=["account", "event_type", "channel"],
                condition=models.Q(person__isnull=True, union__isnull=True),
                name="unique_whole_type_preference",
            ),
            models.UniqueConstraint(
                fields=["account", "event_type", "channel", "person"],
                condition=models.Q(person__isnull=False),
                name="unique_person_preference",
            ),
            models.UniqueConstraint(
                fields=["account", "event_type", "channel", "union"],
                condition=models.Q(union__isnull=False),
                name="unique_union_preference",
            ),
        ]
        ordering = ["-created_at", "pk"]

    def __str__(self) -> str:
        scope = self.person or self.union or "the whole event type"
        return f"{self.account}: {self.get_state_display()} - {self.event_type} for {scope} ({self.channel})"

    def clean(self) -> None:
        # A Broadcast has no per-recipient targeting to override - it's
        # tied to zero or more People for context/audience-narrowing
        # (see Broadcast.people), not as something an account can opt
        # in/out of individually. Only a whole-event-type row (mute/
        # subscribe everything) is meaningful for it. This only catches
        # ModelForm/admin saves (both call full_clean()) - see
        # notifications.views.TogglePersonPreferenceView for the same rule
        # enforced against a raw .create() call.
        if self.event_type_id and self.event_type.code == EventType.BuiltinCode.BROADCAST:
            if self.person_id or self.union_id:
                raise ValidationError(
                    "Broadcasts can only be muted for the whole event type, not per person."
                )


class OccurrenceManager(models.Manager):
    def with_related(self) -> models.QuerySet["Occurrence"]:
        """Joins everything _render_occurrence_message needs to render one occurrence.

        person/union_a/union_b's own father/mother are chained in too -
        Person.parents_label (rendered into every occurrence email via
        notifications/templates/notifications/email/_parents.html) reads
        both, and without this each occurrence's email render cost 4
        extra un-batched Person queries (2 parents x up to 2 people for a
        union-anchored event) - see AGENTS.md's note on this. Shared by
        notifications.tasks.send_due_notifications and
        notifications.views.OccurrencePreviewView, both of which render a
        full occurrence message from just a pk/uuid.
        """
        return self.get_queryset().select_related(
            "person__father",
            "person__mother",
            "union",
            "union__person_a__father",
            "union__person_a__mother",
            "union__person_b__father",
            "union__person_b__mother",
            "event_type",
        )


class Occurrence(models.Model):
    # Looked up by notifications.views.OccurrencePreviewView - every model
    # gets one regardless of whether a view happens to need it yet (see
    # EventType.uuid).
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.CASCADE, related_name="occurrences"
    )
    union = models.ForeignKey(
        Union, null=True, blank=True, on_delete=models.CASCADE, related_name="occurrences"
    )
    event_type = models.ForeignKey(EventType, on_delete=models.CASCADE, related_name="occurrences")

    hebrew_year = models.PositiveIntegerField()
    occurrence_date = models.DateField(help_text="The halachic date of the event this year")
    send_date = models.DateField(
        help_text="The date the notification actually goes out (shifted for Shabbos/Yom Tov)"
    )
    # Which of ShiftReason.SHABBOS/YOM_TOV actually caused send_date to
    # land before occurrence_date - see family.hebrew.resolve_send_date,
    # which produces exactly this list. Stored as plain JSON (a list of
    # already-display-ready strings, e.g. ["Shabbos", "Yom Tov"] - see
    # ShiftReason's own docstring for why there's no separate label to
    # map back from) rather than a boolean, so a later admin edit to
    # EventType.notify_days_before can never retroactively make the
    # *reason* for an already-computed occurrence unrecoverable - only
    # re-deriving it from the live, possibly-changed EventType could do
    # that (see the real bug this replaced: OccurrencePreviewView used
    # to replay resolve_send_date from event_type.notify_days_before at
    # preview time).
    shift_reasons = models.JSONField(default=list, blank=True)

    is_sent = models.BooleanField(default=False)
    computed_at = models.DateTimeField(auto_now_add=True)

    objects = OccurrenceManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["person", "event_type", "hebrew_year"], name="unique_person_occurrence"
            ),
            models.UniqueConstraint(
                fields=["union", "event_type", "hebrew_year"], name="unique_union_occurrence"
            ),
            # Matches Message's own exactly-one-of constraint - app code
            # (_compute_for_subject) always sets exactly one, but nothing
            # else stopped the admin from saving a row with both null or
            # both set, which would later raise AttributeError in
            # Message.family/_occurrence_template_context when
            # send_due_notifications tries to process it.
            models.CheckConstraint(
                condition=(
                    models.Q(person__isnull=False, union__isnull=True)
                    | models.Q(person__isnull=True, union__isnull=False)
                ),
                name="occurrence_exactly_one_of_person_or_union",
            ),
        ]
        indexes = [models.Index(fields=["send_date", "is_sent"])]
        ordering = ["send_date", "occurrence_date", "pk"]

    def __str__(self) -> str:
        subject = self.person or self.union
        return f"{subject} - {self.event_type} {self.hebrew_year}"

    @property
    def shifted_for_shabbat_or_yomtov(self) -> bool:
        """Whether send_date landed before occurrence_date at all, for any reason."""
        return bool(self.shift_reasons)


# The tags/attributes Trix's default toolbar can actually produce (bold,
# italic, strikethrough, link, heading, quote, code, bulleted/numbered
# list - see notifications.widgets.TrixEditorWidget) - anything else (a <script>,
# an inline style, an <img>, ...) either isn't reachable from Trix's UI at
# all or has no business in an email regardless of how it got into the
# field (a crafted POST, a future admin edit, ...). Sanitizing
# unconditionally in Broadcast.save() - not just in the form - means
# every write path is covered, not just the one the UI happens to use
# today.
BROADCAST_ALLOWED_TAGS = {
    "div",
    "br",
    "strong",
    "em",
    "del",
    "a",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "h1",
}

# rel is deliberately not in the allow-list here - nh3's default
# link_rel="noopener noreferrer" already manages that attribute itself
# and rejects it being separately whitelisted.
BROADCAST_ALLOWED_ATTRIBUTES = {"a": {"href", "target"}}


@reversion.register()
class Broadcast(models.Model):
    """A one-off, free-text message an owner/editor sends to the family.

    Unlike everything else in this app, it isn't computed from a Person/
    Union date field, so it never gets an Occurrence row (see
    notifications.tasks.send_due_broadcasts, which sends it directly).

    Muting is whole-type only (see NotificationPreference.clean()) - a
    Broadcast can be tied to one or more People for context and to
    narrow "immediate family only" subscribers down to the recipients'
    own relatives (see notifications.audience.resolve_broadcast_audience),
    but there's no per-recipient targeting or per-person override on top
    of that, on purpose (see AGENTS.md).
    """

    # Used in URLs instead of the database pk, same reasoning as
    # Person.uuid/Union.uuid.
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    family = models.ForeignKey("tenants.Family", on_delete=models.CASCADE, related_name="broadcasts")
    people = models.ManyToManyField(
        Person,
        blank=True,
        related_name="broadcasts",
        help_text="Optional - who this update is about. Narrows 'immediate family only' subscribers "
        "to whoever's immediate family to any of these people; leave blank for a family-wide update.",
    )
    # Rich HTML from Trix (notifications.widgets.TrixEditorWidget), always
    # sanitized before it reaches the database - see save() below. Never
    # rendered into the message a recipient gets except through the
    # broadcast email template (notifications/templates/notifications/
    # email/broadcast.html); the SMS/plain-text renderings strip it back
    # down to plain text instead (see notifications.tasks).
    text = models.TextField()
    created_by = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="broadcasts_created")

    send_at = models.DateTimeField(
        default=timezone.now, help_text="When to send this - leave as-is to send immediately."
    )
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-send_at", "pk"]

    def __str__(self) -> str:
        status = "sent" if self.is_sent else "scheduled for"
        return f"Broadcast to {self.family} ({status} {self.send_at:%Y-%m-%d %H:%M})"

    def save(self, *args, **kwargs) -> None:
        self.text = nh3.clean(self.text, tags=BROADCAST_ALLOWED_TAGS, attributes=BROADCAST_ALLOWED_ATTRIBUTES)
        super().save(*args, **kwargs)


class Message(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    # Not currently referenced over HTTP anywhere, but every model gets one
    # regardless (see EventType.uuid).
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    occurrence = models.ForeignKey(
        Occurrence, null=True, blank=True, on_delete=models.CASCADE, related_name="messages"
    )
    broadcast = models.ForeignKey(
        Broadcast, null=True, blank=True, on_delete=models.CASCADE, related_name="messages"
    )
    account = models.ForeignKey(Account, null=True, on_delete=models.SET_NULL, related_name="messages")
    channel = models.CharField(max_length=10, choices=ChannelEnum.choices())
    destination = models.CharField(max_length=255)

    subject = models.CharField(max_length=255, blank=True)
    # The plain-text part every client falls back to (auto-derived from
    # html_body for email - see notifications.tasks - or the actual SMS
    # text for that channel). Always populated; html_body is email-only
    # and blank for SMS.
    body = models.TextField()
    html_body = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    # The raw success payload from send_email/send_sms - only ever set
    # once a send succeeds, see notifications.tasks.send_message. `error`
    # is where a failed attempt's detail lives instead.
    provider_response = models.JSONField(blank=True, null=True)
    error = models.TextField(blank=True)
    tries = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(occurrence__isnull=False, broadcast__isnull=True)
                    | models.Q(occurrence__isnull=True, broadcast__isnull=False)
                ),
                name="message_exactly_one_of_occurrence_or_broadcast",
            ),
        ]
        ordering = ["-created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.channel} to {self.destination}: {self.subject or self.body[:40]}"

    @property
    def family(self) -> Family:
        """The family this message was sent on behalf of.

        occurrence.person.family, occurrence.union.person_a.family (same
        either-side-works reasoning as elsewhere for a Union), or
        broadcast.family for the exactly-one-of-the-two this model
        enforces. Used to pick the right Family.sms_sender_id for an SMS
        send - see notifications.tasks.send_message.
        """
        if self.occurrence:
            person = self.occurrence.person
            return person.family if person else self.occurrence.union.person_a.family
        return self.broadcast.family
