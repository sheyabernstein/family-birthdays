# notifications — implementation notes

Loaded automatically when Claude Code reads files under `notifications/`.
Deep rationale for this app's own decisions - see the root `AGENTS.md`
for the project-wide orientation these build on.

## Event types & scheduling

- **Life events are user-defined, but a few come for free.** Birthday,
  yahrzeit, and anniversary exist as global defaults every family starts
  with, plus Bar/Bat Mitzvah, which supersedes that one year's Birthday
  occurrence when a boy turns 13 or a girl turns 12 — but only when
  `dob_hebrew_year` is explicitly known, per the never-derive-Hebrew-
  from-Gregorian rule above. Families can add their own event types on
  top of all of these.
- **An EventType can fire once or every year, and ahead of the date or on
  it.** `recurs=False` is for something that only happens once (Wedding -
  the marriage itself; the yearly celebration of it afterwards is the
  separate, `recurs=True` Anniversary type) - it's computed only for the
  Hebrew year the anchor date actually fell in, not every year that
  month/day comes around. `notify_days_before` shifts the send date
  ahead of the actual occurrence (0, the field's own default, sends on
  the day itself - still right for Yahrzeit and a custom family event
  type with no established lead-time convention of its own; Birthday
  uses 1, Bar/Bat Mitzvah 3, Wedding 7 - see the data migration in
  `notifications/migrations/0006_default_notify_days_before.py` for
  where these built-in overrides actually live). See
  `notifications/tasks._compute_for_subject`.
- **A computed `send_date` can land in the past relative to "today" the
  moment the row is created, and every consumer of it has to treat that
  as normal, not a bug to work around by excluding it.**
  `family.hebrew.resolve_send_date` walks a notification backward across
  Shabbos/Yom Tov (never forward - "day before," never "day after," so
  nobody's expected to check their phone on the day itself). That's
  fine when an occurrence is computed well ahead of time (the nightly
  `compute_occurrences` sweep, 400 days out), but
  `compute_occurrences_for_person`/`_union` - the *immediate*, single-
  subject recompute triggered by a `Person`/`Union` save (see
  `family.signals` below) - can run on the anchor date
  itself, and if that date is Yom Tov, the walk-back lands `send_date`
  before "today" on arrival (found for real: a union anchored on 1
  Tishrei, edited on Rosh Hashanah itself). Both places that read
  `send_date` account for this: `send_due_notifications` filters
  `send_date__lte=today`, not `send_date=today` - an exact match would
  silently and permanently skip a row like this (and, separately, self-
  heals a missed day - a worker outage, say - the same way
  `send_due_broadcasts`' `send_at__lte` already did); `notifications.
  tasks._delete_stale_unsent_occurrences` filters on `is_sent=False`
  alone, not also `send_date__gte=today` (an earlier version did, and it
  was a real bug, not a refinement - it made exactly this kind of row
  permanently uncleanable too, alongside being unsendable).
  `_clear_superseded_occurrence_types`, doing the same kind of cleanup
  for birthday/bar/bat-mitzvah, never had this bug - it only ever
  filtered on `is_sent=False`, which is the pattern to copy for any
  future "find occurrences that no longer apply" query: key off
  `is_sent`, never off `send_date` being in the future.
  `family.views.DashboardView` had the same bug in reverse: it filtered
  `send_date__gte=today` alone for "Upcoming," which would silently drop
  a row exactly like the ones above (real, unsent, just stuck with a
  `send_date` that already landed in the past) instead of still showing
  it. Fixed the same way - `Q(send_date__gte=today) | Q(is_sent=False)` -
  rather than dropping the date filter entirely, since Upcoming still
  shouldn't show already-sent history.

## Notification preferences

- **Notification preferences resolve most-specific-wins, across three
  levels**: a person/union-specific override beats an account's own
  per-event-type setting (subscribed to everyone, muted entirely, or
  limited to their immediate family — spouse/parent/child/sibling, a
  fixed definition, not configurable per family), which beats
  `EventType.default_opt_in` if the account hasn't touched that event
  type at all. See `notifications/audience.py` for the resolution order
  and `notifications.models.NotificationPreference`. A whole Person can
  also be excluded (`notifications_enabled=False`) for someone who's
  recorded for lineage only, e.g. an in-law's own parent who isn't
  really part of this family.

## Broadcasts

- **A Broadcast is a one-off, free-text update - the odd one out among
  event types, since it's sent directly rather than computed from an
  anchor date.** Owner/editor only (`notifications.views.BroadcastListView`/
  `BroadcastCreateView`/`BroadcastUpdateView`/`BroadcastDeleteView`,
  `/broadcasts/` - moved into `notifications`, not `family`, once it became
  clear the whole feature revolves around the `Broadcast` model that
  already lived there, not `Person`/`Union`; `notifications.forms.
  BroadcastForm` and `notifications.widgets.TrixEditorWidget` moved with
  it), optionally tied to one or more People (a couple is
  just selecting both spouses - there's no separate Union link) for
  context and to narrow "immediate family only" subscribers to whoever's
  immediate family to *any* tied person, not literally everyone
  (`notifications.audience.resolve_broadcast_audience` unions the
  per-person results). It reuses the `EventType`/`NotificationPreference`
  mute machinery via a seeded global `broadcast` `EventType`
  (seeded in `notifications/migrations/0001_initial.py`'s
  `seed_global_event_types`) so
  muting it is just the same whole-event-type toggle as any other type on
  My Notifications - but unlike every other type, it has **no per-person
  override**: `NotificationPreference.clean()` rejects a `person`/`union`-
  scoped row for it, `PersonDetailView` never renders a toggle for it, and
  `TogglePersonPreferenceView` 404s if asked for one anyway. It never gets
  an `Occurrence` row (`EventType.anchor` is blank for it, and it's
  excluded from `notifications.tasks._event_types_by_family` via
  `NON_SCHEDULED_CODES`, same as Bar/Bat Mitzvah) - instead, `send_at`
  (defaulting to now, editable up until it's actually sent) is picked up
  directly by `notifications.tasks.send_due_broadcasts`, a Celery Beat
  task running every 5 minutes that atomically claims each due row
  (`Broadcast.objects.filter(pk=..., is_sent=False).update(is_sent=True)`,
  only proceeding if that affected a row) before building `Message` rows
  and sending - the same claim-then-send pattern that makes editing or
  cancelling (`BroadcastUpdateView`/`BroadcastDeleteView`, both 404 once
  `is_sent`) safe against a race with the poller, and makes the polling
  itself safe against overlapping runs. Polling against
  the database (not a Celery ETA-scheduled task) is deliberate: an ETA
  task only lives in a worker's own memory until it fires, so it's lost
  outright by a worker restart (a routine event here, given how often
  this app deploys) or a Redis flush before delivery - a scheduled
  broadcast surviving both is worth a per-minute query.
  `notifications.tasks.send_due_notifications` follows the same
  claim-then-send shape for `Occurrence` - it originally set `is_sent`
  only *after* sending, which meant a crash/retry mid-loop or an
  overlapping run could re-send the same occurrence's notifications to
  the whole family; fixed to claim (`Occurrence.objects.filter(pk=...,
  is_sent=False).update(is_sent=True)`) before rendering/sending, exactly
  mirroring `send_due_broadcasts` above. `Message.
  occurrence`/`Message.broadcast` are mutually exclusive (`Message.Meta`'s
  `message_exactly_one_of_occurrence_or_broadcast` constraint) - a
  Broadcast's messages never touch the Occurrence-based pipeline at all.
  `Occurrence.person`/`Occurrence.union` have the matching
  `occurrence_exactly_one_of_person_or_union` constraint, added after the
  admin (no `readonly_fields` on `OccurrenceAdmin`) turned out to be able
  to save a row with both or neither set, which would later blow up in
  `Message.family`/`_occurrence_template_context` once
  `send_due_notifications` tried to process it - app code always sets
  exactly one, but nothing enforced it at the DB level until this matched
  `Message`'s own constraint.
  A Broadcast never shows up on the dashboard (`family.views.
  DashboardView`) - "Upcoming" is specifically the computed, anchor-date
  events every other event type produces; a broadcast's own home is the
  Broadcasts page. `created_by` is tracked on the row (see visibility
  rule below, and it's shown on `broadcast_list.html`) but deliberately
  left out of the message body itself (`notifications.tasks.
  _render_broadcast_message`) - a recipient sees it as the family
  speaking, not a named individual. **A pending (not yet sent) broadcast
  is only visible - and editable/cancelable - to whoever created it, or a
  Django superuser**; a sent one is visible to every owner/editor, as
  shared history. `notifications.views._visible_broadcasts` is the single
  place that resolves this, used by `BroadcastListView`,
  `BroadcastCreateView.form_invalid`, `BroadcastUpdateView`, and
  `BroadcastDeleteView` alike - one editor scheduling a broadcast isn't
  something every other editor needs to see (or be able to hijack) before
  it's actually gone out, but once it has, it's the family's shared
  record of what was said. `BroadcastListView` itself stays gated to
  owners/editors (`FamilyEditorRequiredMixin`) regardless - a plain
  member never sees the Broadcasts page at all. **`BroadcastListView`
  splits the one queryset into `scheduled_broadcasts`/`sent_broadcasts`
  in Python** (opposite orderings - scheduled reads soonest-first,
  what's coming up next; sent stays `Broadcast.Meta`'s own `-send_at`,
  newest-first, right for a history log - simplest to build both from
  one already-fetched list rather than expressing two orderings in one
  query), rendered as two separate `.timeline` lists (see the Upcoming/
  Broadcasts timeline bullet below) - never one list filtered inline in
  the template, since each broadcast used to get its own single-item
  `<ul>` that way, breaking the connecting line into disconnected
  segments. `.timeline-entry` doesn't zero its own `margin-bottom` on
  `:last-child` (an earlier version did) - a section header
  (`<h2>Sent</h2>`) routinely follows a `.timeline` immediately, and
  bare `h2`/`h3` have no top margin of their own (see the base type
  rules above) - relying on the *next* element's margin for that gap
  means a heading right after a timeline needs the timeline's own
  trailing space to not read as glued on. This is also why the earlier
  single-item-per-broadcast bug (previous paragraph) was worse than it
  looked: every entry being both first *and* last child zeroed out
  every entry's spacing at once, not just the final one's.

## Timeline UI

- **Upcoming and Broadcasts both use a `.timeline` list, not the
  `.ledger` one still used by `subscriptions.html`'s "Exceptions for
  specific people" section.** `.ledger` is a compact, genuinely
  one-line-per-row format that's the right fit for a short "person ·
  channel · state" override row; it was never a good fit for Upcoming
  (a birthday and a bar mitzvah landing on the same day used to render
  as two full, separately-date-stamped rows) or a broadcast's own
  variable-length rich text (nested in a card inside a ledger row,
  competing visually with the actual page's own cards). `.timeline`
  gives each point in time its own card, connected by a vertical rule,
  with a colored dot per anchor type (sage/wine/slate matching
  `.badge-birth`/`.badge-death`/`.badge-marriage`) or per status
  (`.timeline-scheduled`, brass, for a still-pending broadcast).
  `family.views.DashboardView` groups occurrences landing on the *same*
  `(send_date, occurrence_date)` pair - deliberately not `send_date`
  alone, since two different people's occurrences can share a send_date
  by coincidence (independent Shabbos/Yom Tov shifts landing on the same
  day) without sharing the same underlying Hebrew date, and grouping on
  send_date alone would show one of them under the wrong Hebrew-date
  stamp - and orders the underlying queryset explicitly by `(send_date,
  occurrence_date, event_type__name)` so multiple event types sharing a
  timeline point appear in a stable, predictable order rather than
  whatever the DB happens to return. Each event's badge sits in a fixed-
  width grid column (`.timeline-who`) specifically so the name always
  starts at the same offset regardless of the badge's own text length
  ("Bar Mitzvah" vs "Birthday") - an earlier version let the name
  immediately follow the badge inline, which looked misaligned row to
  row purely because badge labels aren't all the same width.

## Per-channel message rendering (email/SMS)

- **Every event type gets its own template, per channel - subject/body
  are composed from the actual occurrence/broadcast relationships at
  render time, not hardcoded per-type Python strings.**
  `notifications/templates/notifications/email/<code>.html` and
  `.../sms/<code>.txt` (one pair per `EventType.BuiltinCode`, plus
  `_default.html`/`_default.txt` for a family's own custom event type -
  Django's `render_to_string([specific, default], ...)` list-fallback
  picks whichever exists) are real Django templates rendered against the
  occurrence's own `person`/`union`/`event_type`/dates (or, for a
  Broadcast, its `text`/`people`/`family`) in `notifications.tasks.
  _render_occurrence_message`/`_render_broadcast_message` - see those
  for the full per-channel contract. Rendering still happens once, when
  the `Message` row is created (not on every later read) - `Message.
  subject`/`body`/`html_body` are the persisted result, the same
  audit-trail reasoning as before this template rewrite, just backed by
  real templates instead of f-strings now.
  - **A late send says so, in both the subject and the body - and an
    early one (shifted for Shabbos/Yom Tov) says *when*, not just
    "today".** Because `send_date` can land before "today" (see the
    bullet above) and `send_due_notifications` deliberately catches up
    on it rather than dropping it, a `Message` can genuinely be rendered
    days after its `occurrence_date`. `_occurrence_template_context`
    computes `is_late = occurrence.occurrence_date < timezone.localdate()`
    once, and every occurrence template (email + SMS, including
    `_default`) branches on it for tense ("is"/"was"), then renders
    *when* via `occurrence.occurrence_date|naturalday` (humanize's
    filter - reads "today"/"tomorrow"/"yesterday" for a 1-day gap either
    direction, a formatted date beyond that) rather than hardcoding
    "Today is ...". `_occurrence_subject` takes `is_late` as a keyword
    argument and uses the same `naturalday` call so the subject line
    never disagrees with the body it's paired with - this was a real bug
    caught by hand-rendering a late message during development: the body
    had been fixed to say "was ...", but the subject still
    unconditionally said "today" until the caller was updated to pass
    `is_late` through. Wedding's on-time wording is "coming up", not a
    date, regardless of lateness - it's sent several days
    (`notify_days_before`, see the data migration in
    `notifications/migrations/0006_default_notify_days_before.py`)
    ahead of the actual date, so "today" was never accurate for it even
    in the normal case. **`naturalday` earns its place here for a second, later
    bug, not just the first one**: `shifted_for_shabbat_or_yomtov`
    (`occurrence_date` moved *earlier* than the real anchor, on purpose)
    is deliberately not `is_late` - `is_late` only fires when
    `occurrence_date` itself is in the past, never when it's still ahead
    of "today" - but every template's own "not late" branch used to
    hardcode "Today is ..." regardless, which is simply false when the
    real anchor is tomorrow (or later) and only the *notification* went
    out early. Shipped to production for real (a birthday whose Hebrew
    date fell on Yom Tov: sent a day ahead, on time, correctly not
    "late" - and every email/SMS about it confidently said "Today is
    ..." for a birthday that was actually the next day) before
    `naturalday` replaced the hardcoded wording - the same filter call
    covers the on-time case and the shifted-early case for free, with no
    separate `is_today`-style flag needed. `django.contrib.humanize` is
    in `INSTALLED_APPS` for exactly this one filter.
  - **Email** gets a real HTML body (`Message.html_body`) built on
    `templates/email/_base.html` - a table-based layout (for mail-client
    compatibility) with a `@media (prefers-color-scheme: dark)` block
    overriding background/text/link colors only, not a full re-theme.
    Apple/iOS Mail honor it; Gmail's support is partial and Outlook
    desktop ignores it outright, so those just show the light version -
    an accepted trade-off, not a bug to chase further (see
    `notifications.services.send_email`'s own docstring for the
    multipart mechanics: `Message.body`, the plain-text fallback every
    client falls back to, is auto-derived from the HTML via
    `notifications.helpers.html_to_plain_text` rather than hand-authored
    a second time). **This base template lives at the top level, not
    under `notifications/`** - it moved there once `accounts`' own
    sign-in email (see the Person/Account bullet below) needed the same
    shared header/card/footer chrome, mirroring `templates/base.html`'s
    own existing top-level convention for the web UI's shared shell.
    Every concrete email template across both apps still extends it via
    the same `{% extends "email/_base.html" %}`. Its header/footer
    default to a family's name via `{{ family_name|default:"Family
    Tree" }}` - blank/omitted `family_name` (the sign-in email, when an
    account belongs to zero or several families) falls back to the
    generic brand, matching `send_email`'s own no-family default. The
    footer is a `{% block footer %}` specifically so the sign-in email
    can override it - "Manage notification settings" doesn't apply
    there, and would be actively confusing to show on something
    security-sensitive someone might not even trust yet. **The outer,
    full-bleed `<table class="page-bg" style="background:#eeebe1">`
    wrapping the whole layout needs its own dark-mode class override,
    same as `.paper`/`.ink`/etc - it isn't enough to override `body`'s
    own background alone.** Found for real (every email this app had
    ever sent had this bug, not just the sign-in one - it just took a
    second template to notice it): some clients don't reliably render a
    plain `<body>` background at all, which is why that inline
    background exists on the table in the first place, but leaving it
    untargeted meant it sat on top of `body`'s own dark override with no
    override of its own, so the page stayed visibly light everywhere
    outside the card even in a client that otherwise fully honors
    `prefers-color-scheme: dark`.
  - **Every email's `html` is run through `notifications.services.
    _inline_css` before it's attached** - `css-inline` (Rust-backed;
    `premailer`, the older stdlib-of-email-inlining choice, hasn't
    shipped since 2021 and doesn't declare support past Python 3.8) copies
    every plain rule from the `<style>` block directly onto each
    element's own `style="..."` attribute. This exists because Mailpit's
    own "HTML Check" tab (open any sent email, that tab) quantifies
    exactly why: a plain `<style>` block is only ~61% fully supported
    across real clients, `background` ~58%, and a `<body>` background is
    outright unsupported in a third of them - a client in that gap
    doesn't get a *different-looking* email, it gets an unstyled one,
    colors and all. An inline `style` attribute is close to universally
    honored, so inlining converts most of that gap into "renders
    correctly" instead. `keep_at_rules=True` is the one setting that
    actually matters - `@media (prefers-color-scheme: dark)` can't be
    resolved statically at send time (it depends on the recipient's own
    client), so it's left as real CSS in a slim surviving `<style>` tag
    rather than collapsed away; `keep_style_tags=False` just drops the
    now-redundant plain rules that already got copied onto elements
    directly. **A light-mode rule that leans on `!important` becomes an
    inlined `!important` too, and per the CSS spec that beats a
    same-`!important` class-selector rule in the surviving `@media`
    block purely on inline's own higher specificity** - found for real
    via `.btn-email-text` in `templates/email/_base.html`, whose light-
    mode rule had a stray `!important` it never actually needed (a plain
    class selector already outranks the plain `a` rule it was guarding
    against); removing it was the actual fix, not something `_inline_css`
    itself needed to work around. Falls back to the un-inlined HTML on a
    genuine `css_inline.InlineError` rather than blocking the send over
    what's purely a rendering enhancement.
  - **SMS** is plain text, its own short template per type, hard-
    truncated to `notifications.tasks.SMS_CHAR_BUDGET` (160 - one GSM-7
    segment, chosen deliberately conservative over a 2-segment budget so
    copy stays terse and a long Hebrew name pushing the message into
    UCS-2 encoding - which halves the per-segment limit - can't silently
    split it into two texts) via `_truncate_for_sms`, applied even to
    the built-in per-type templates (a defensively long name could still
    blow the budget) and to a Broadcast's own SMS rendering (raw HTML
    stripped down to plain text first, not sent as markup). This budget is
    app policy, not provider-specific - it doesn't change with the SMS
    backend below, since it exists to keep every text a single GSM-7
    segment regardless of who's actually sending it.
  - **The actual SMS send is a pluggable backend** (`notifications/sms.py`),
    selected via `settings.SMS_BACKEND` - a dotted class path, same shape
    as `STORAGES["staticfiles"]["BACKEND"]`, not a magic string like the
    old `"console"`. `SmsBackend` is the interface (`send(to, body,
    sender_id) -> dict`); `ConsoleSmsBackend` (the default, for dev/tests)
    just logs; `SnsSmsBackend` sends via AWS SNS's `Publish` API.
    `notifications.services.send_sms` resolves and caches one backend
    instance (`functools.lru_cache`, invalidated on `SMS_BACKEND` changing
    via `setting_changed`, so `override_settings` still works in tests) -
    not re-resolved per send, and not built at import/settings-load time
    either, since a boto3 client constructed in a Celery prefork worker's
    parent process before fork is exactly the kind of thing that can
    misbehave post-fork. `SnsSmsBackend` needs `AWS_ACCESS_KEY_ID`/
    `AWS_SECRET_ACCESS_KEY`/`AWS_SNS_REGION` (passed explicitly to boto3,
    not left to its own default credential chain, so every SMS-relevant
    setting lives in one place) - each backend owns its own
    `SmsBackend.validate_settings()` classmethod (a no-op on the base
    class; `SnsSmsBackend`'s enforces all three are set) rather than a
    separate helper elsewhere hardcoding which dotted path needs what,
    since that knowledge belongs with the class that actually needs it -
    adding a future backend with different requirements means implementing
    this classmethod, nothing else. Called once from
    `NotificationsConfig.ready()` (`notifications/apps.py`, same
    `AppConfig.ready()` convention as `family/signals.py`'s wiring), not
    from `config/settings.py` directly like `check_email_security_settings`
    - resolving the configured class means importing `notifications.sms`,
    which reads `django.conf.settings`, and `config/settings.py` would
    still be mid-execution as the exact module that settings object
    resolves to at that point. `ready()` runs after settings and the app
    registry are both fully loaded, so this still fails loudly at real
    startup (`manage.py`, gunicorn, and Celery workers all call
    `django.setup()`) - just not for a bare script importing `config.
    settings` without going through `django.setup()`. Alphanumeric Sender ID
    (`AWS.SNS.SMS.SenderID`, from `Family.sms_sender_id`) isn't supported
    in the US/Canada - AWS silently drops it there and sends from a
    shared/random long code instead, not a bug to chase. **Some SNS
    failures aren't worth retrying** - a bad phone number or bad IAM auth
    will fail identically on every attempt - so `SnsSmsBackend.send`
    re-raises those specific `botocore` error codes as
    `SmsUnrecoverableError`; `notifications.tasks.send_message` catches
    that separately from a generic send failure and skips `self.retry()`
    entirely (logged at `error`, then re-raised so the task still shows up
    as a real `FAILURE` in the Task Results admin, not a silent success) -
    every other exception still goes through the existing retry path
    unchanged.
  - **Sender branding is per-family, not per-message-type.** This app
    serves many families from what's normally one shared sending number
    (SMS) and one shared sending domain (email), so without something
    naming which family a message is from, an SMS is anonymous and
    someone in more than one family (see the Person/Account bullet below)
    couldn't tell two families' texts apart at all. `Family.sms_sender_id`
    (alphanumeric, 10 chars - a real carrier constraint, not an arbitrary
    one) is editable from Workspace Settings
    (`tenants.forms.FamilySenderSettingsForm`, `tenants.views.
    FamilySettingsView`) by whoever holds the global `tenants.change_family`
    permission - a site admin grant (Django staff via the admin), the
    same "site admin" concept `tenants.add_family` uses for
    `CreateFamilyView` - not a family role. This is a genuinely global
    Django permission, not scoped to one family, so it isn't self-service
    for an ordinary family owner the way most other per-family settings
    are; a family's own owner/editor sees the current values read-only on
    that page. It can also be set at creation time (`tenants.views.
    CreateFamilyView`, itself gated on `tenants.add_family`) - blank falls
    back to `notifications.services.DEFAULT_SMS_SENDER_ID` ("FamilyTree")
    rather than sending unbranded.
    Email doesn't need an equivalent settable field for the display name -
    it's just `Family.name` (there's no separate "email sender name"
    concept) - but the address itself *is* per-family:
    `Family.sender_email` is a computed property, not a stored field
    (`noreply-{slug}@settings.EMAIL_SENDING_DOMAIN` - derived from `slug`
    so it can't drift if the family's later renamed, since `slug` is set
    once at creation and never changes - see `Family.save()`).
    `EMAIL_SENDING_DOMAIN` is deliberately a separate setting from
    `SITE_BASE_URL` (the app's own served-at scheme+host, used for links/
    static URLs) - `SITE_BASE_URL`'s own dev default, `http://localhost:8000`,
    isn't even a valid email domain (a scheme and a port aren't legal
    there), and in production the sending domain is often its own
    subdomain anyway, kept separate so
    DKIM/SPF/DMARC reputation for mail doesn't tangle with the web app's
    domain. One domain, verified once at the domain level (SES, not
    per-address) - an arbitrary per-family local-part just works with no
    additional per-family provider setup. `DEFAULT_FROM_EMAIL` (the
    fallback address used for a message with no family context at all -
    the sign-in email, when an account belongs to zero or several
    families - see the Person/Account bullet below) is computed the same
    way, `f"notifications@{EMAIL_SENDING_DOMAIN}"`, rather than its own
    independently-set env var - it used to be, but an independent
    `DEFAULT_FROM_EMAIL` could point at a domain SES never verified,
    while deriving it from `EMAIL_SENDING_DOMAIN` guarantees the fallback
    address lives on the exact same domain every per-family address
    already depends on being verified. `Family.reply_to_email` is a
    genuinely optional third field - blank omits the `Reply-To` header
    entirely rather than defaulting to anything, so a reply just goes
    nowhere useful, same as it already would without the feature.
    `notifications.services.send_email` needed rebuilding directly on
    `EmailMultiAlternatives` for this - the `send_mail()` shortcut it used
    before has no way to set `Reply-To` at all.
    `notifications.tasks.send_message` resolves all of this via
    `Message.family` (occurrence's person/union, or broadcast, whichever
    this exactly-one-of-the-two message has - see the model's own
    `Meta.constraints`). This is also why a Broadcast's own SMS template
    doesn't repeat the family name in the message body the way its email
    template's subject line still does - the sender ID already carries
    that, and repeating it would just cost budget for no reason (see the
    SMS bullet above).
  - **Icons**: one PNG per `EventType.badge_class` (the same anchor-
    based bucketing the in-app badges already use - see that property's
    own docstring), under `notifications/static/notifications/img/event-icons/`
    (`birth.png`/`death.png`/`marriage.png`/`broadcast.png`, plus
    `default.png` for a blank-anchor custom type), linked via a real
    hosted `<img src>` - `notifications.services.static_absolute_url`,
    exposed to templates as `{% event_icon_url %}` - rather than a
    base64 `data:` URI. This app used to embed these as base64 (see git
    history) since a hosted URL has a real staleness problem: `Message.
    html_body` is rendered once and persisted (see above), and
    `STORAGES["staticfiles"]` is WhiteNoise's
    `CompressedManifestStaticFilesStorage`, which content-hashes every
    file's URL (`birth.a1b2c3d4.png`) - the Dockerfile's multi-stage
    build runs `collectstatic` fresh into a new image on every build
    with nothing carrying the previous image's `staticfiles/` forward,
    so an icon's bytes changing would silently break every
    already-delivered email's hashed URL. Base64 sidestepped that at
    the cost of a worse one, confirmed for real by sending a live test
    email: Gmail (web and app) never renders a `data:` URI image at
    all, full stop, while an ordinary hosted image just falls under
    Gmail's completely normal "images are blocked until you click
    Display images below" gate - the same experience as any other
    email with images, and strictly better than an image that never
    renders regardless of what the recipient clicks. The staleness
    problem is solved directly instead: `config.storage.
    StableStaticFilesStorage` (a thin `CompressedManifestStaticFilesStorage`
    subclass) overrides `file_hash()` to return `None` for anything
    under `notifications/img/event-icons/` - a supported Django
    extension point, since `HashedFilesMixin.hashed_name()` treats a
    falsy `file_hash()` as "leave this filename alone" - so these icons
    keep a stable, un-hashed URL forever while every other static asset
    (`app.css`, the JS libs) still gets the normal cache-busted hashed
    name. The one remaining edge case - an icon's bytes changing after
    emails referencing the old ones have already gone out - just means
    an old email silently shows the new icon on next render, not a
    broken image or a 404; a fair trade given how rarely these change.
    `static_absolute_url` builds off `settings.SITE_BASE_URL`, never
    `EMAIL_SENDING_DOMAIN` - the two are independent on purpose (see
    the sender-branding bullet below), and there's no reason a static
    asset's URL should depend on which domain a message happens to be
    sent from.
  - **The subject's own Hebrew first name, and a short "Parent &
    Parent's FirstName" label, both show on every occurrence email/SMS
    except Broadcast** (`Person.parents_label`, `family.templatetags.
    family_extras.with_hebrew_first_name`, included via
    `notifications/templates/notifications/{email/_parents.html,
    sms/_parents.txt}` for both people on a Union-anchored event). This
    was actually only ever implemented for email at first - the SMS
    templates went untouched for a while despite this doc already
    claiming otherwise, caught later by inspection rather than a test
    (the email/SMS tests lived in the same file but never cross-checked
    each other's coverage). Fixing it also surfaced a real, separate,
    pre-existing bug: these plain-text `.txt` templates still had
    Django's normal HTML autoescaping active, so `parents_label`'s own
    " & " rendered as the literal text "&amp;" in an actual SMS - fixed
    with `{% autoescape off %}` around each template's content. The same
    class of bug existed independently in `notifications.helpers.
    html_to_plain_text` (email's plain-text fallback) and the Broadcast
    SMS body's own inline `strip_tags()` call - `strip_tags()` only
    removes tag *markup*, it never decodes entities, so a name or
    Broadcast author's own "&"/apostrophe survived as `&amp;`/`&#x27;`
    long after HTML tags were gone. Both now run `html.unescape()`
    *after* `strip_tags()`, never before - unescaping first would turn
    someone's literal, intentionally-typed "&lt;b&gt;" into "<b>" text
    that `strip_tags()` would then wrongly treat as real markup and
    remove. Shown unconditionally, not
    just when a name happens to collide with someone else's - it reads
    as a warm, personal touch either way ("Shloime & Bruchele's Blimi"
    is the way you'd actually refer to someone in a large family
    conversationally), and it's especially valuable once names repeat:
    large families that name children after grandparents produce a lot
    of exact repeats (three different "Blimi Rokach"s with three
    completely different parents, seen for real in this app's own data
    - the Hebrew first name alone doesn't reliably tell them apart
    either, since two of the three happened to share the same one too).
    Only *living, tracked* parents are used - an untracked one
    (`notifications_enabled=False`, e.g. an in-law's own parent entered
    only so the tree renders) is a lineage-only stub outside the
    family's actual active sphere, and naming one would read as
    confusing rather than helpful. Both the parents and the subject are
    shown by first name/nickname only in that line - the surname's
    already given in full in the sentence right above it, so repeating
    it three times in one short phrase would be the opposite of
    concise. The phrasing itself is deliberate, not a formal
    genealogical statement - "child of..." was considered and rejected
    as too clinical for what should read as a warm, personal
    notification.
  - **Broadcast rich text**: authored via Trix (`notifications.widgets.
    TrixEditorWidget` - a hidden `<input>` plus a bound `<trix-editor>`
    custom element, CDN-loaded like Tom Select), but never trusted as-
    is - `Broadcast.save()` unconditionally sanitizes `self.text` with
    `nh3` against `BROADCAST_ALLOWED_TAGS`/`_ALLOWED_ATTRIBUTES` (the
    tags Trix's own default toolbar can actually produce - bold,
    italic, strikethrough, link, heading, quote, code, lists - nothing
    Trix can't produce has any business surviving regardless of how it
    got into the field). Sanitizing in the model, not just the form,
    means every write path is covered, not just the one the UI happens
    to use today. File attachments (Trix supports drag/drop by default)
    are disabled outright, client-side (`notifications/static/notifications/js/broadcast_editor.js`
    cancels `trix-file-accept`; the toolbar's attach button is hidden in
    `app.css`, with `!important` - confirmed directly against trix.css
    that an unqualified attribute selector ties on specificity with
    Trix's own `.trix-button-group` rule and loses on load order alone)
    - there's no upload endpoint for a dropped file to go to, and the
    sanitizer drops an `<img>` regardless.

## Broadcast people picker

- **The broadcast people picker excludes untracked people** (`notifications.
  forms.BroadcastForm`, `notifications_enabled=False` - see the
  "tracked" bullet above) - unlike the father/mother/spouse pickers,
  where an untracked stub is a completely normal, correct choice, an
  untracked person never gets notified of anything, a broadcast
  included, so there's nothing to pick them *for*. Same "never drops
  what's already assigned" exception as `_parent_queryset`, though: a
  person already tied to a broadcast before becoming untracked stays in
  that broadcast's own queryset so editing it doesn't silently drop them.

## Task scheduling, retries, and rate limiting

- **`notifications.tasks.send_message` retries with exponential backoff
  via Celery's own `autoretry_for`/`retry_backoff`, not a manual
  `self.retry(exc=exc)` on a flat delay.** `autoretry_for=(Exception,)`
  covers a generic provider failure and `SmsRateLimitedError` (below)
  alike; `dont_autoretry_for=(SmsUnrecoverableError,)` excludes the one
  failure retrying can never fix - a bad phone number will fail
  identically on every attempt, so it fails the `Message` immediately
  instead. **The actual delays are much shorter than `retry_backoff_max=
  600` makes them look** - Celery's backoff formula is `factor *
  2**retries` (factor is 1 here, from a bare `retry_backoff=True`),
  jittered and capped at `retry_backoff_max`; with `max_retries=3` the
  raw values are only 1s/2s/4s before jitter, so the 600s ceiling never
  actually engages (it'd take ~9 retries at this factor to approach it).
  Verified for real against a live, non-eager worker (real Redis broker,
  real Celery retry scheduling, not `CELERY_TASK_ALWAYS_EAGER`): a
  message that raised `SmsRateLimitedError` twice then succeeded was
  retried at +0.03s and +2.0s, and ended up `Message.Status.SENT` with
  no error recorded - confirming both that autoretry_for genuinely
  re-queues the task through the broker (not just Celery's in-memory
  eager-mode shortcut) and that it lands on a real success once the
  transient condition clears. This window suits `SmsRateLimitedError`
  well (SNS's own budget resets every second - see below), but is worth
  knowing if `send_message` is ever expected to ride out a longer,
  genuine SNS/network outage: with `max_retries=3` capped this low, the
  whole retry sequence for a *generic* provider failure spans single-
  digit seconds, not the "up to several minutes" a `retry_backoff_max=
  600` reads like at a glance.
- **`config.enums.TaskPriority` is a 3-tier enum of Celery *queue names*
  (`HIGH="high"`/`NORMAL="normal"`/`LOW="low"`), passed as `queue=` on
  every `@shared_task`, not Celery/kombu's own per-message Redis
  priority (`Task.apply_async`'s `priority=` kwarg /
  `broker_transport_options["priority_steps"]`).** `HIGH` is the
  magic-link sign-in task (`accounts.tasks.send_magic_link_message` - a
  human is watching the sign-in page right now, both email and SMS),
  `NORMAL` is an actual notification send (`notifications.tasks.
  send_message`), `LOW` is scheduled/background sweeps
  (`compute_occurrences`/`send_due_notifications`/`send_due_broadcasts`).
  Lives in `config/enums.py`, not `notifications/enums.py` - it's not a
  notifications-specific concept, `accounts` needs it too.
  **The per-message Redis priority approach was tried first and hit a
  live kombu 5.6.2 bug, found by testing this for real against a running
  docker stack, not by reasoning about it in the abstract**: the moment
  any task actually carried a non-zero priority, the worker's own pidbox
  control-command replies (mingle/heartbeat traffic, always priority 0)
  started throwing `ValueError: not enough values to unpack` from
  kombu's exchange lookup - and worse than just a logged error, the
  worker silently stopped consuming the priority-suffixed queue
  afterward, needing a restart to drain the backlog. Reproduced directly:
  queued a real magic-link task, watched it sit unconsumed in Redis
  (`LRANGE`/`LLEN` on the raw `celery:1` list) until the worker
  restarted. The fix is three genuinely separate Celery queues consumed
  by the single worker process, in a fixed order, via
  `queue_order_strategy: "priority"` in `CELERY_BROKER_TRANSPORT_OPTIONS`
  (`config/settings.py`) - kombu's own docs describe this mode plainly:
  "Consume from queues in original order, so that if the first queue
  always contains messages, the rest of the queues in the list will
  never be consumed from." That's real, deterministic priority ordering
  that never sets a per-message Redis priority at all, so it never
  touches the buggy code path above - confirmed after the fix by
  stopping the worker, queuing 5 LOW tasks then 1 HIGH task, and
  restarting: the HIGH task was received and completed before any of the
  5 LOW ones, with zero control-command errors across the run.
  `docker/entrypoints/worker.sh`'s `celery worker -Q high,normal,low`
  is what actually declares the consumption order - order matters there,
  not just membership. `CELERY_TASK_DEFAULT_QUEUE = TaskPriority.NORMAL`
  covers anything dispatched with no explicit `queue=` (Celery's own
  built-in `backend_cleanup` periodic task, notably) so it still lands
  somewhere the worker is listening. `CELERY_WORKER_PREFETCH_MULTIPLIER=1`
  is still required regardless of this change: otherwise the worker
  prefetches a batch of low-priority tasks before a high-priority one
  ever gets a chance to jump the line, silently defeating the whole
  point of separate queues.
- **AWS SNS's real 10 req/s account-wide `Publish` limit is enforced with
  a Redis-backed global counter checked inside `SnsSmsBackend.send()`
  (`notifications/sms.py`'s `_check_sns_publish_rate_limit`), not
  Celery's own per-worker `rate_limit=`.** Celery's `rate_limit` is
  strictly per-worker-process - confirmed against Celery's own docs and
  a related upstream issue - so it can't enforce an account-wide AWS
  limit correctly once more than one worker/replica exists; the only way
  to get a true global limit natively is one queue with exactly one
  pinned consumer, which this app deliberately doesn't do (that's a
  standing infra cost for a genuinely small volume). A plain `INCR`+
  `EXPIRE` fixed-window counter, keyed by the current second and shared
  across every process, is a much simpler fit. `SNS_PUBLISH_RATE_LIMIT_
  PER_SECOND` (`config/settings.py`, default `8` - a safety margin under
  the real `10`) is the budget; going over it raises `SmsRateLimitedError`
  before the real `publish()` call ever happens. **This exception is a
  plain `Exception`, deliberately not a `FamilyBirthdaysError`** -
  unlike `SmsUnrecoverableError`, it's transient by construction (budget
  frees up every second), so `send_message`'s `autoretry_for=(Exception,)`
  picks it up like any other retryable failure, and it's logged at a
  quiet level rather than raised as an alarming error - a Celery
  intermediate retry never fires `task_failure` in the first place (only
  the attempt after `max_retries` exhausted does), so this only ever
  becomes Sentry-visible if it persists through every retry, which is the
  one case actually worth knowing about.
