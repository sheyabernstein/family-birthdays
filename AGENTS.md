# Family Birthdays

## What this is

A private, multi-family life-events ledger. Each extended family gets its own
isolated space to record their family tree — births, deaths, marriages — and
get notified about birthdays, yahrzeits (death anniversaries), and wedding
anniversaries on the correct day.

## Why it exists

The whole point is that Jewish life-cycle observances run on the Hebrew
calendar, not the Gregorian one, and getting that wrong isn't a cosmetic bug —
it means notifying someone on the wrong day for a yahrzeit. So this app treats
the Hebrew date as the thing that actually drives scheduling: every person's
birth/death, and every marriage, is recorded in both calendars, and the
recurring anniversary is recomputed against the Hebrew calendar every year
(handling leap-year Adar splits and short-month edge cases along the way).
Notifications that would otherwise land on Shabbat or Yom Tov go out the day
before instead, since nobody's checking their phone that day.

## Who uses it

Anyone in an extended family, signed in via a passwordless magic link (email
or SMS, no accounts to manage). Within a family, everyone can view the tree
and manage their own notification subscriptions; owners and editors can add
and edit people and marriages; only the owner can remove someone from the
ledger. A person can belong to more than one family (e.g. the one you were
born into and the one you married into), and switches between them like
switching workspaces.

## Core ideas

- **A family is a tenant.** Each family's people, marriages, and custom event
  types are its own — visible to nobody else, with one deliberate exception:
  an in-law reachable through a marriage is visible to both sides, since
  that's a real shared connection, not a data leak.
- **What a role can do is resolved in exactly one place:**
  `tenants.permissions.resolve_family_permissions`, not re-derived from
  `request.family_role` independently everywhere it matters. It returns a
  `FamilyPermissions(can_edit, can_delete)` - `can_edit` is owner or
  editor (create/edit people and unions); `can_delete` is owner only
  (removing a person or marriage is a real, hard `DELETE`, not a
  soft-delete - see `PersonDeleteView`/`UnionDeleteView` - genuinely
  harder to undo than an edit, which is why it's narrower on purpose,
  not an oversight). `CurrentFamilyMiddleware` resolves this once per
  request onto `request.family_permissions`, right alongside
  `request.family_role`; `FamilyEditorRequiredMixin`/
  `FamilyOwnerRequiredMixin` (`tenants/mixins.py`) check it there, and
  every role-gated template check (`person_detail.html`,
  `person_list.html`, `help.html`'s owner/editor section) reads
  `request.family_permissions.can_edit`/`.can_delete` instead of
  comparing `request.family_role` to `"owner"`/`"editor"` inline - a
  dozen independent copies of that comparison is exactly what would
  drift out of sync with the mixins if the role model ever changed.
  `/help/`'s own "Roles" section is visible to everyone (not just
  owner/editor - a plain member benefits from knowing what each role
  can do too) and states the viewer's own current role directly
  (`request.family_role`, when resolved).
- **`FamilyEditorRequiredMixin`/`FamilyOwnerRequiredMixin` override
  `dispatch()` by hand instead of building on Django's own
  `UserPassesTestMixin`, on purpose.** Each needs to *chain* into
  `FamilyRequiredMixin`'s own `dispatch()` via `super()` and still produce
  two genuinely different failure responses depending on which layer
  actually fails: no family at all falls through to `FamilyRequiredMixin`'s
  own redirect (to the switcher, to create-family, or to no-access,
  depending on membership state), while the wrong role for an *existing*
  family raises a specific `PermissionDenied` message instead.
  `UserPassesTestMixin.test_func()` only ever returns a bool - the
  response on failure is fixed by `handle_no_permission()` - so it can't
  express "redirect for this reason, 403-with-this-message for that one"
  stacked in a single MRO chain the way overriding `dispatch()` and
  calling `super()` naturally can (see the null-guard in
  `FamilyEditorRequiredMixin.dispatch()`: `if request.family is not None
  and not can_edit` deliberately defers to `FamilyRequiredMixin`'s
  redirect when there's no family selected, rather than showing a
  confusing "wrong role" 403 to someone who simply hasn't picked one
  yet). That said, `UserPassesTestMixin` is exactly the right tool for a
  *simple*, non-chaining predicate - `tenants.views.FamilySettingsView`
  uses it for its own per-HTTP-method check (viewing the page is open to
  any member; only `POST`-ing changes needs `role == OWNER` - narrower
  than the usual editor-level bar, since branding affects how every
  member's email/SMS looks, not just ledger data), since there's
  nothing else to chain into there.
- **A role check is not a tenant check - `FamilyEditorRequiredMixin`/
  `FamilyOwnerRequiredMixin` only prove the viewer can edit/delete
  *something* in their own family, never that the specific record a URL
  points at (a person's or union's UUID) actually belongs to it.**
  That's `tenants.mixins.FamilyScopedMixin`'s job, mixed into every view
  that fetches one specific record for editing/deleting
  (`PersonUpdateView`/`PersonDeleteView`, `UnionUpdateView`/
  `UnionDeleteView`, `BroadcastUpdateView`/`BroadcastDeleteView`) via a
  declared `family_lookup` - one field path (`"family"`) or several,
  OR'd together (`["person_a__family", "person_b__family"]`, for Union's
  deliberate either-side-can-edit exception - see the in-law bullet
  below). A subclass that doesn't set `family_lookup` raises
  `ImproperlyConfigured` the moment the class is defined (import time,
  in effect app-startup time), not on first request - a missing filter
  used to just fail silently (`Model.objects.all()`, every family's
  rows) and rely on whoever wrote the view remembering to scope it by
  hand, which is exactly the kind of per-view discipline this app
  otherwise refuses to rely on (see the role-check bullet above). A view
  whose correct scoping is more than just "belongs to this family" (e.g.
  Broadcast's pending-visibility rule) still mixes this in for the
  tenant-boundary half via `family_lookup`, then layers its own extra
  condition on top of `super().get_queryset()` rather than reinventing
  the family filter itself - see `notifications.views._own_or_sent_q` and
  `BroadcastUpdateView`/`BroadcastDeleteView`. `PersonDetailView`/
  `FamilyTreeView` deliberately don't use this mixin - viewing (not
  editing) an in-law reached through a marriage is supposed to succeed
  across the tenant boundary (see `family.access.person_is_visible`),
  which is a different, broader rule than what's safe to edit.
- **The two calendars are recorded independently, never derived.** Nothing
  in the system computes a Hebrew date from a Gregorian one (or vice
  versa) and treats it as authoritative — that conversion is ambiguous
  whenever the event happened after sunset, and only a person entering the
  data actually knows the answer.
- **Every Hebrew date shown to a user drops the year's thousands digit** -
  `תשפ"ו`, not `ה' תשפ"ו`. The `ה'` means 5000, which is true of every
  date this app will ever show (every family record and every future
  Occurrence falls in the 5000s), so spelling it out is noise, not
  information. `hdate`'s own `str(HebrewDate(...))` always includes it
  with no option to turn it off, so `family.hebrew.format_hebrew_date`
  reimplements that same one-line format using `year % 1000` instead -
  every user-facing Hebrew date (the `hebrew_str` template filter, and
  `Person.dob_hebrew_display`/`dod_hebrew_display`/
  `Union.marriage_hebrew_display`) goes through it rather than calling
  `str()` on a `HebrewDate` directly.
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
  ahead of the actual occurrence (0, the default, sends on the day
  itself - right for a birthday or yahrzeit; Wedding uses 3). See
  `notifications/tasks._compute_for_subject`.
- **A computed `send_date` can land in the past relative to "today" the
  moment the row is created, and every consumer of it has to treat that
  as normal, not a bug to work around by excluding it.**
  `family.hebrew.resolve_send_date` walks a notification backward across
  Shabbat/Yom Tov (never forward - "day before," never "day after," so
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
- **The immediate recompute is a `post_save` signal, not a call
  sprinkled across every view that touches a `Person`/`Union`.**
  `family/signals.py`'s `_recompute_person_occurrences`/
  `_recompute_union_occurrences` (wired up in `FamilyConfig.ready()`)
  call `compute_occurrences_for_person`/`_union` on every save,
  unconditionally - no diffing which fields actually changed, the same
  "just recompute, don't trust what was there before" policy the code
  already used before this was a signal. That "don't diff" choice is
  deliberate, not a missed optimization: the fields that matter aren't
  confined to one model (a `Person`'s own death date changes their
  `Union`'s eligibility - see the marriage-goes-stale-on-death bullet
  below), so a "which fields are relevant" safelist would just be the
  same bug-prone bookkeeping this signal exists to eliminate, moved one
  layer down. Before this, the same call was duplicated across
  `PersonCreateView`/`PersonUpdateView`/`UnionCreateView`/
  `UnionUpdateView`'s own `form_valid()`s - correct as long as everyone
  remembered to keep adding it to every new write path, and the Django
  admin (`PersonAdmin`/`UnionAdmin` have no `save_model()` override)
  never got it at all, silently relying on the nightly sweep to catch
  up. A signal fixes both for free: single source of truth, and every
  write path (the real forms, the admin, a future API, a one-off shell
  script) gets it automatically with zero coordination required. Each
  receiver wraps its recompute in a bare `try`/`except`, logging and
  swallowing rather than raising - a recompute bug shouldn't turn an
  otherwise-successful save into a 500; the nightly `compute_occurrences()`
  sweep is still the self-healing backstop regardless. The one real gap
  a signal doesn't close: `post_save` never fires for `bulk_create()`/
  `bulk_update()`/`QuerySet.update()` - a future bulk data migration
  touching many `Person`/`Union` rows that way won't get immediate
  recompute (same nightly-sweep backstop applies, just with up to a
  day's lag) unless it calls `compute_occurrences_for_person`/`_union`
  itself per row.
- **There's no "engaged" status to remember to flip to "married" later.**
  A Union is recorded as `MARRIED` from the moment it's added, even when
  `marriage_date_gregorian`/the Hebrew anchor is still in the future -
  `Union.is_upcoming` (computed, never stored) is what "not married yet"
  actually means here. It drives the family tree's "Engaged to ..."
  badge (`family/tree_chart.py`'s `upcoming_wedding_partner`, rendered in
  `family_tree.html`), and gates which of the Wedding/Anniversary toggles
  shows on a person's page (`family/views.PersonDetailView`) - Wedding
  before the date, Anniversary after. The one-time Wedding notification
  itself (see above) still only fires in the actual anchor year, so it
  isn't tied to `is_upcoming` directly.
- **A subscription toggle that's no longer relevant is hidden, not left
  to be quietly wrong.** Both `family/views.PersonDetailView` and the
  actual scheduling logic in `notifications/tasks.py` apply the same
  rules, since a hidden toggle whose Occurrence still gets computed
  behind it is worse than not hiding it at all: Birthday and Bar/Bat
  Mitzvah stop once `not person.is_living` (only Yahrzeit applies from
  then on - symmetric to how Yahrzeit itself only ever applied once
  `not is_living`); Anniversary and Wedding stop once either spouse has
  died, with no manual status flip required (same "computed, not stored"
  reasoning as `Union.is_upcoming` above); Bar/Bat Mitzvah's age-13/12
  cutoff is `notifications.tasks.person_has_passed_coming_of_age`, which
  prefers the Hebrew birth year over `Person.age` - `age` is Gregorian-
  only and is `None` for the common case of someone with only a Hebrew
  birth year recorded, which silently never triggers the cutoff.
  `family.signals._recompute_person_occurrences` recomputes the saved
  person's own unions' occurrences too, not just their own, since
  recording a death there is exactly the moment that can turn an
  existing Anniversary stale.
- **`Union.Status.WIDOWED`/`DIVORCED` aren't made redundant by the above -
  they're a second, independent path to the same suppression.** The
  automatic death-based check needs a death actually recorded
  (`Person.is_living`); manually setting a union's status to anything but
  `MARRIED` suppresses every union event type for it immediately
  (`status != MARRIED` is checked before anything else in
  `_event_types_for_union`/`PersonDetailView`), which matters when
  someone knows a spouse died but hasn't entered `dod_gregorian`/
  `dod_hebrew_year` yet - and it's also what surfaces the fact itself as
  a "Spouse · Widowed" label on `person_detail.html`, which the
  is_living check has no way to express on its own. `DIVORCED` has no
  automatic equivalent at all - nothing else in the data implies it.
- **A "tracked" person is just one with `notifications_enabled=True`.**
  The People list hides anyone with it off by default (they're lineage-
  only stubs - an in-law's own parent, entered so the tree renders, not
  someone anyone's searching for by name) - see
  `family/views.PersonListView`. An owner/editor can reveal them via
  `?show_untracked=1`; anyone can still reach one through the tree or a
  tracked relation's own profile, since neither of those filters on it.
  **An untracked person never gets an event computed for them, full
  stop - including a union they're part of.** `notifications.tasks.
  _subject_pairs`/`compute_occurrences_for_person` already skip a
  Person with `notifications_enabled=False`; `union_is_eligible_for_
  notifications` (`notifications/tasks.py`) is the same rule extended to
  a Union - a marriage doesn't get Wedding/Anniversary occurrences (and
  nobody gets notified about it) unless *both* spouses are tracked, not
  just both living. Toggling the flag takes effect immediately, in both
  directions, for free: `family.signals._recompute_person_occurrences`
  already recomputes the saved person's own occurrences and every union
  they're in on every save (see the marriage-goes-stale-on-death bullet
  above) - turning tracking off makes that recompute find nothing
  eligible and delete the not-yet-sent future occurrences accordingly
  (already-sent ones stay as history); turning it back on makes the same
  recompute generate fresh ones. `family.views.PersonDetailView` mirrors this on
  the toggle UI itself: the "Notify me" card's toggles never appear for
  an untracked person (they'd be dead controls - nothing to toggle), and
  a union row is left out of "Anniversaries" the same way whenever
  either spouse is untracked. An owner/editor still sees a specific
  "isn't tracked for notifications" message with a link to fix it, since
  they're the only ones who can act on it; anyone else just doesn't see
  the card at all when it'd have nothing to show.
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
  by coincidence (independent Shabbat/Yom Tov shifts landing on the same
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
    early one (shifted for Shabbat/Yom Tov) says *when*, not just
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
    date, regardless of lateness - it's sent `notify_days_before=3` ahead
    of the actual date, so "today" was never accurate for it even in the
    normal case. **`naturalday` earns its place here for a second, later
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
- **A person record (Person) and a login (Account) are different things,
  on purpose.** Person is per-family ledger data; Account is the one
  global sign-in identity a human has, since the same person can in
  principle appear as a Person in more than one family's ledger (their
  birth family and the one they married into) and should still only ever
  have one login. Contact info is entered on the Person form for
  convenience, but it's actually read from and written to the linked
  Account — see `family/forms.PersonForm` for the find-or-create-and-link
  flow that runs whenever an admin adds contact info to a Person.
  **`PersonForm.clean()` refuses to link an Account that's already linked
  to a Person in a *different* family**, even though that's exactly the
  married-into-two-families case above - the form can't tell "this is
  genuinely the same person, and whoever's adding them here has their
  consent" apart from "this is an arbitrary stranger's real email/phone",
  and the same bug covers both readings: `save()`'s "already linked"
  branch overwrites the shared Account's login email/phone directly, so
  letting the match through would let one family silently corrupt another
  family's real login contact info the next time either one edits it, and
  `family_role` grants instant editor/owner access with no confirmation
  from the account holder either way. Linking the same Account across two
  families for real is still possible, just not self-service through this
  form - it takes an admin/shell action (or the Django admin directly).
  There's no separate invite step for the normal (single-family) case:
  nobody gets an Account without a Person record first - though this is a
  convention `PersonForm` follows, not a DB constraint, so an Account
  created some other way (the Django admin, a one-off shell fix) can still
  end up with zero linked Person rows. That matters beyond just "can't see
  whose it is": `notifications.audience.is_immediate_family` resolves
  *from* the viewer's own linked Person, so an unlinked Account fails
  closed as "not immediate family" for literally everyone, including their
  own parents - a real bug once, not hypothetical. **Editing your own
  contact info once you already have an
  Account is self-service, from My Notifications' Channels card**
  (`accounts.forms.AccountContactForm`, `accounts.views.
  UpdateAccountSettingsView` - both live in `accounts`, not `family`,
  since they only ever touch the `Account` model, with no Person
  provisioning involved) - a different, simpler path than
  `PersonForm`'s: you're editing `request.user` directly, so there's no
  find-or-create-or-link decision to make, and `Account.email`/`phone`'s
  own `unique=True` covers the collision case Person Form has to check
  explicitly. This does *not* cover linking an Account to a Person in
  the first place (the bootstrapping case above) - that still only
  happens through `PersonForm`, owner/editor only.
- **The magic-link sign-in email/SMS is the one message this app sends
  that isn't obviously any one family's** - every other Occurrence/
  Broadcast message has an unambiguous `Message.family` to brand from
  (see the sender-branding bullet above); a sign-in link is requested
  before an account's picked a workspace for this session, and the same
  account can belong to several families at once. `accounts.views.
  _sole_family` (a module-level helper, called from
  `RequestMagicLinkView.post`) resolves this the same way `Message.
  family`'s own exactly-one-of-the-two constraint does elsewhere: brand
  with a family's own name/`sender_email`/`reply_to_email`/
  `sms_sender_id` only when the account belongs to *exactly* one, never
  an arbitrary pick among several - a member of two families seeing one
  of them named on a sign-in email would misrepresent who's actually
  sending it. Zero or multiple memberships both fall back to the same
  blank-everything default `notifications.services.send_email`/
  `send_sms` already use for a message with no family context at all -
  no new fallback path was needed for this. `accounts/templates/accounts/
  email/sign_in.html` is otherwise a normal `templates/email/_base.html`
  extension (see that bullet above) - a call-to-action button (`.btn-
  email-bg`/`.btn-email-text` in the shared base's own `<style>`, kept
  there rather than in this one template since any future call-to-action
  email would want the same button, and its light/dark background+text
  need to invert *together* or the button vanishes against a card
  background that just flipped to match it) with the same raw URL
  repeated underneath in small text, for a client that strips the link
  or someone who'd rather copy-paste. The link's expiry is templated
  from `accounts.magic_links.TOKEN_TTL_SECONDS` (in both the email and
  the SMS body) rather than a hardcoded "15 minutes" - the two used to
  be independent literals with no actual link to the real constant, so
  a TTL change had no way to keep the copy honest.
- **Migration history is squashed to one `0001_initial.py` per app**
  (`accounts`, `family`, `notifications`, `tenants`), replacing the
  iterative history accumulated during development (five files for
  `notifications` alone, several `AlterField`/`AddConstraint` passes per
  app as models settled). Done once, deliberately, before the first real
  commit - not a pattern to repeat casually later, since squashing after
  other environments have applied the old migrations breaks them (their
  `django_migrations` rows reference migration names that would no longer
  exist as files). `notifications/migrations/0001_initial.py` folds in
  what used to be five separate `RunPython` seed migrations (global event
  types, coming-of-age types, wedding, broadcast, Sites config) as two
  `RunPython` calls at the end of one file, rather than splitting seeding
  from schema across several files the way the original history did.
  Squashing itself never touches real data or a live schema - the new
  files produce byte-for-byte the same end state
  (`makemigrations --check` against the pre-squash models confirmed "No
  changes detected" before *and* after), so an already-migrated database
  only needs its `django_migrations` bookkeeping brought in line (delete
  the old per-app rows, let the new `0001_initial` row already match by
  name, or `migrate --fake` if the name changed) - never a real
  `migrate`/data dump-and-restore. A fresh database (a new environment, or
  the test suite's own from-scratch DB) still runs these for real,
  including the seed `RunPython`s, and that path is what
  `poetry run pytest` exercises on every run.
- **Every meaningful edit is versioned**, via django-reversion — who
  changed what and when, with a diff, for Person/Union/Family/
  FamilyMembership/EventType/NotificationPreference/Account. Visible in
  the Django admin's history view on each of those. Don't reinvent this
  per-model; register new mutable models the same way instead. A
  person's own detail page also surfaces this directly, owner/editor
  only: the "History" card (`family/history.py`'s `person_history`,
  capped at `HISTORY_LIMIT`) diffs consecutive `Version.field_dict`
  snapshots for the Person plus every Union they're a party to, resolving
  FK fields (father/mother/account, person_a/person_b) to the related
  object's own name via `Model._meta` introspection rather than showing
  a raw id. Reversion was only added partway through this project's
  life, and a local, untracked bulk-import script (outside the repo -
  not something an agent working in this codebase needs to find) runs
  outside any request so it's never tracked at all - the first *tracked* version
  for an older record is labeled "Earliest recorded version", not
  "Record created", unless its timestamp actually lines up with the
  object's own `created_at`. Each changed field renders as its own table
  row (`HistoryEventChange`, rowspan-grouped by When/Who/On in
  `person_detail.html`) with a two-line red/green GitHub-PR-style diff
  block, not several changes crammed into one cell - a "created"/
  "earliest version" event has no diff to show, so it's just the plain
  label spanning those columns instead.
- **The family tree UI is a third-party library, not hand-rolled.**
  `family-chart` (MIT, D3-based — see `family/tree_chart.py` and
  `family/templates/family/family_tree.html`) handles layout, pan/zoom,
  and expand-on-click for arbitrarily large trees; don't rebuild that
  from scratch when what's needed is a config change or a card-rendering
  tweak.
- **Editing from the tree redirects to the real forms - it never opens a
  second, parallel edit UI.** The "+ Add father/mother/spouse/child"
  placeholders (owner/editor only, see `can_edit_tree` in
  `FamilyTreeView`) are synthetic nodes spliced into the chart's own data
  purely so `family-chart`'s layout engine can position them - clicking
  one redirects to `PersonCreateView`/`UnionCreateView` with the relation
  prefilled via query params (`father`/`mother`, or `link_as`+`link_of`
  for the reverse "add a parent" direction), not `family-chart`'s own
  `EditTree` inline-form system. That system's generic field set (name/
  birthday/gender) can't represent this app's actual Person schema
  (dual-calendar dates, Account linking, yahrzeit observance, ...), so
  adopting it would mean validating the same data two different ways.
- **The father/mother/spouse pickers are searchable, gender-filtered, and
  labeled with a birth year - not a plain `<select>` of everyone in the
  family.** A ledger this size has plenty of repeated names (two "Blimi
  Rokach"s is normal), so a bare name can't disambiguate them - every
  option shows `(b. 1994)` or `(b. year unknown)` via a
  `label_from_instance` override, using helpers from `family/widgets.py`
  (`_person_option_label`/`_parent_option_label`) - that module, not
  `forms.py`, is where the picker's own rendering concerns live; `forms.py`
  only wires the widget/queryset/label into each field. The queryset
  itself is filtered by gender (a father can't be female) and, for
  father/mother, excludes the person's own descendants
  (`Person.descendant_ids()`) so no one can become their own ancestor.
  The widget is Tom Select (CDN script/CSS in `person_form.html`/
  `union_form.html`, retinted in `app.css`) for type-to-filter, following
  the same CDN-for-a-small-JS-library pattern as `family-chart`.
  **The gender filter never drops whoever's already assigned, even if
  their own gender is wrong** (`family.forms._parent_queryset`) - a
  father/mother field pointing at the wrong-gender person is bad import
  data this app didn't create, and hiding that value from the dropdown
  would silently null it out the next time the record is saved rather
  than surfacing it. `_parent_option_label` labels it "wrong gender on
  file" instead.
- **The Tom Select dropdown itself renders a two-line option, not the
  plain label text** - name + birth year on top, the person's Hebrew
  first name as an RTL subtitle underneath (`family/static/family/js/person_picker.js`,
  `.ts-option-row` in `app.css`), since a same-script name is often the
  fastest way to tell two same-named relatives apart. Tom Select doesn't
  read arbitrary `data-*` attributes off the source `<option>` elements
  on its own (confirmed directly against the library - it only picks up
  value/text/disabled), so `PersonPickerSelect.create_option()`
  (`family/widgets.py`) adds `data-display-name`/`data-birth-year`/
  `data-hebrew-first-name`/`data-warning` attributes, and
  `person_picker.js` re-reads each option's `.dataset` after Tom Select
  initializes and merges them in with `ts.updateOption()` before
  anything renders. The Hebrew first name is also a real search field
  (`searchField: ["text", "hebrewFirstName"]`), not just a subtitle -
  typing it filters the list too. It's omitted from the option
  entirely when it *is* the person's `display_name` (someone with no
  English name recorded) - the subtitle would just repeat the name
  already shown on top. `initPersonPicker` also raises Tom Select's own
  `maxOptions` (default 50) to 10000 - the default silently truncated
  the dropdown well short of a real ledger's size (a couple hundred
  people is normal here), so scrolling to the bottom of the list looked
  like "that's everyone" when it was really just wherever the cap cut
  off. This is purely a render cap, not a search cap - Tom Select always
  searches every underlying `<option>` regardless of `maxOptions` - so
  it only ever bit browsing the full list with no search text typed yet.
- **Every phone `<input>` gets the same intl-tel-input widget, wired
  through one shared helper.** `accounts/static/accounts/js/phone_input.js`'s
  `initPhoneInput(selector)` lives under `accounts` (not `family`, even
  though its first call site is `PersonForm.phone`) because every phone
  field on the site is ultimately a front end onto `Account.phone` - see
  the Person/Account bullet below - not a real field on whatever model's
  form happens to render it; `notifications/templates/notifications/
  subscriptions.html`'s own phone field (`accounts.forms.
  AccountContactForm`, editing `Account.phone` directly) uses the exact
  same helper, following the asset-lives-with-its-subject-matter
  convention in "Static files" above. It adds a country-flag picker and
  client-side validation (CDN script/CSS, same pattern as Tom Select/
  Trix), converting the field's visible value to E.164 right before
  submit (`iti.getNumber()`) so `Account.phone`'s own "stored in E.164"
  convention holds with no server-side change needed - the field posts
  the same format the backend already expects regardless of what country
  format the user actually typed in. A blank value is always treated as
  acceptable (every phone field on this site is optional at the
  form-field level; the whole-form "email or phone required" rule, where
  it exists, is enforced elsewhere), so validation only ever fires on
  genuinely typed-but-invalid input, not on an empty field.
  **`static/css/app.css`'s retinting is deliberately scoped to `.iti
  input[type="tel"]`, not added to the shared generic `input[type="..."]`
  selector list the way Tom Select's fields are** - found the hard way:
  the library reserves `padding-left` on the input for its own flag/
  dial-code button (absolutely positioned over the input's left edge),
  and the app's stylesheet loading after the CDN one meant adding
  `input[type="tel"]` there clobbered exactly that property, leaving the
  flag button with nowhere to go and visually colliding with the
  field's own text. The scoped rule sets every other property (font,
  border, background, color, vertical padding) and leaves `padding-left`
  alone on purpose.
- **Every email `<input>` gets the same assistive, server-side validation,
  wired through one shared helper - and it's never authoritative.**
  `accounts/static/accounts/js/email_input.js`'s `initEmailValidation
  (selector, url)` calls `accounts.views.ValidateEmailView` (`POST
  /accounts/validate-email/`, login-required, same "thin AJAX endpoint
  wrapping a pure function" shape as `family.views.GregorianToHebrewView`)
  on blur and again at submit if the value changed since the last check.
  `accounts.helpers.validate_email_address` does three things Django's own
  `EmailField`/`EmailValidator` doesn't: a domain/TLD typo suggestion
  (stdlib `difflib` against a short common-domain/TLD list - a whole-
  domain match wins over a same-domain TLD-only match, since running both
  independently on an uncorrected domain produces a redundant, worse
  second suggestion for the same typo), and a real MX/A-record lookup
  (`dnspython`) that treats a domain as unable to receive mail only when
  it's *definitively* so - genuinely nonexistent (`NXDOMAIN`) or an
  explicit RFC 7505 "null MX" (a lone `.` exchange - `example.com` is a
  real, live instance of this, not hypothetical) - never when the lookup
  merely *fails* (timeout, unreachable nameserver): "couldn't check" is
  always treated as acceptable, so a transient DNS hiccup can't
  manufacture a false rejection. None of this touches the actual save
  path - `PersonForm.email`/`AccountContactForm.email` are still just
  Django's own `EmailField` at submit time, so a network failure here
  degrades to "no extra help," never to "can't save." The JS mirrors that
  same fail-open stance: a failed/errored fetch is treated as acceptable
  rather than invalid. Submission is blocked in the browser only
  (`event.preventDefault()`, re-focusing the field) when the *server*
  returned a definite `valid: false` - this is UX polish, not a second
  authoritative gate, so a request crafted directly against the real
  endpoints is still only ever checked by Django's own validator, exactly
  as before this feature existed.
- **The broadcast people picker excludes untracked people** (`notifications.
  forms.BroadcastForm`, `notifications_enabled=False` - see the
  "tracked" bullet above) - unlike the father/mother/spouse pickers,
  where an untracked stub is a completely normal, correct choice, an
  untracked person never gets notified of anything, a broadcast
  included, so there's nothing to pick them *for*. Same "never drops
  what's already assigned" exception as `_parent_queryset`, though: a
  person already tied to a broadcast before becoming untracked stays in
  that broadcast's own queryset so editing it doesn't silently drop them.
- **The Hebrew date fields can be prefilled from the Gregorian one, but
  never authoritatively.** `family.views.GregorianToHebrewView` (POST-
  only, login-required, `family:gregorian_to_hebrew`) is a thin wrapper
  around `family.hebrew.gregorian_to_hebrew` returning `{year, month,
  day}` JSON - `family/static/family/js/hebrew_autofill.js` calls it on the Gregorian
  date field's `change` event and fills the Hebrew year/month/day inputs
  next to it (Born/Death in `person_form.html`, Marriage in
  `union_form.html`). This only ever fires when all three Hebrew fields
  are still empty - it can never clobber a value someone already typed
  in, including one it just prefilled a moment ago and the user then
  edited - and a `help-text` note appears next to the filled-in fields
  naming the after-sunset caveat explicitly (and disappears the moment
  any of the three fields is hand-edited), so a prefilled value is never
  mistaken for one someone actually confirmed. This doesn't relax the
  never-derive-Hebrew-from-Gregorian rule above - the stored value is
  whatever ends up in the form fields at submit time, prefilled or
  hand-typed, and nothing marks or treats a prefilled value differently
  once it's saved.
- **Every model gets a `uuid` field, and any value that crosses an HTTP
  boundary identifies a record by it, never by the integer pk** — a URL
  segment, a form field, a POST param. A pk hints at record count and, more
  importantly, is guessable/enumerable: `notifications.views` used to look
  up `EventType`/`Person`/`Union` by a plain `..._id` POST field holding
  `.pk`, and `tenants.views.SwitchFamilyView` did the same for `family_id` -
  a member of one family could guess or enumerate another family's ids.
  Every model now has `uuid = models.UUIDField(default=uuid.uuid4,
  editable=False, unique=True)` (`Person`/`Union`/`Broadcast` first; the
  rest added together in one pass once the pattern was made a standing
  convention rather than a per-model judgment call), and every hand-rolled
  identifier uses it. Note this only stops *guessing* an id - it doesn't by
  itself re-check that the record still belongs to the account making the
  request, which is why e.g. the `EventType` lookups above also keep an
  explicit `family_id not in (None, request.family.id)` guard: a
  previously-valid, now-stale uuid (a bookmarked link from before an
  account left a family) shouldn't still work either. Django admin is
  exempt - it's a separate, staff-only, Django-managed interface that
  already addresses everything by pk consistently, and changing that isn't
  this app's own code. The migration files that originally added `uuid` to
  each existing table this way no longer exist on disk (see the squashed-
  migrations bullet below), but the technique is still the one to reach
  for whenever a *new* unique field needs backfilling onto a table that
  already has rows: add it nullable/non-unique first, backfill a distinct
  value per row via `RunPython`, then `AlterField` to lock down
  `unique=True` - a plain `AddField(unique=True, default=uuid.uuid4)`
  fails at `makemigrations` time, since Django won't silently call a
  callable default once per existing row for you.
- **Beat runs embedded in the worker, not as its own container.** RedBeat
  (`CELERY_BEAT_SCHEDULER` in `config/settings.py`) keeps the schedule in
  Redis with a leader lock, instead of `django_celery_beat`'s
  DatabaseScheduler, specifically so `celery worker -B` (the setting
  alone selects RedBeat, no `-S`/`--scheduler` flag needed) is safe even
  if the worker service is scaled to more than one replica — see
  `docker-compose.yml`. The schedule itself is
  still the static `CELERY_BEAT_SCHEDULE` dict in code, not admin-editable;
  there's no requirement for families or site admins to change *when*
  things run, only to see that they did. That visibility is
  `notifications.views.ScheduledTasksView` (the static schedule, read-only)
  plus `django-celery-results`' own admin page (`TaskResult` — actual run
  history, status, tracebacks), both linked from the Django admin index.
- **`SITE_BASE_URL` is one `proto://fqdn` setting, not a domain + a
  use-https flag, and not `django.contrib.sites`.** Used wherever an
  absolute URL needs building with no request in play -
  `notifications.services.absolute_url` (the email footer's "Manage
  notification settings" link) and the sign-in magic link
  (`accounts.views.RequestMagicLinkView`, which deliberately doesn't use
  `request.build_absolute_uri()` - see that view's own comment on why:
  this app always terminates TLS upstream via a reverse proxy, so
  `request.is_secure()` is never a reliable signal for what scheme to
  use). This app briefly used `django.contrib.sites` for the domain half
  (seeded from an env var by a migration, then "ordinary admin-editable
  data" from then on) - dropped before the first real deploy once it
  became clear that admin-editability bought nothing: changing the real
  domain also means updating `ALLOWED_HOSTS`/DNS/the TLS cert, all of
  which need a redeploy anyway, so a DB row that could be edited
  independently of those just risked drifting out of sync with them, not
  offering real flexibility. A single `SITE_BASE_URL` (rather than two
  settings that have to agree, `SITE_DOMAIN` + `SITE_USE_HTTPS`) was a
  deliberate choice too, made against this file's own general preference
  for decomposed config (see the Redis bullet just below) - a
  scheme+host pair is one value with one meaning here, not two
  independent knobs that could each be reused separately elsewhere the
  way `REDIS_HOST`/`_PORT`/`_DB`/`_PASSWORD` are.
- **Redis config is discrete env vars (`REDIS_HOST`/`_PORT`/`_DB`/
  `_PASSWORD`), not a single `REDIS_URL`** - mirrors the `POSTGRES_*`
  pattern for the same reason (`config/settings.py` builds the actual
  connection URL itself, so the only real config dependency stays
  python-dotenv). `REDIS_PASSWORD` is genuinely optional - blank omits
  the auth segment from the built URL entirely rather than including an
  empty one, since an empty `:@` userinfo segment makes redis-py attempt
  an (incorrect) empty-password `AUTH` against a server that has none
  configured. `docker-compose.yml`'s `redis` service requires one
  (`--requirepass ${REDIS_PASSWORD}`) and persists to a named volume via
  AOF (`--appendonly yes`) - without that, the Celery broker queue and
  RedBeat's own schedule/leader-lock state started from empty on every
  container restart. Confirmed Celery doesn't leak the password into
  `docker logs` when connecting - its own connection-established log
  line masks it (`Connected to redis://:**@redis:6379/0`) by default,
  so no extra redaction was needed on our side.
- **`tenants.management.commands.migrate_with_lock` talks to Redis
  directly** (`redis.from_url(settings.REDIS_URL)`), the same pattern as
  `accounts/magic_links.py` (the two are now the only modules that do this)
  - not Django's cache framework. There's no `CACHES` setting in
  `config/settings.py`, so `django.core.cache.cache` is per-process
  `LocMemCache` by default; a distributed migration lock built on it would
  give every pod its own useless local lock, silently defeating the whole
  point (confirmed directly against a running container: the lock key never
  showed up in `redis-cli KEYS "*"`, only in each pod's own memory). The
  lock's value is `hostname:8-char-uuid` - the hostname alone can't
  distinguish "the lock this process holds right now" from "a lock this
  same host held earlier that already expired and was re-acquired by
  someone else" (that's what the uuid suffix is for), but a bare uuid on
  its own makes `redis-cli GET`/the structured logs useless for "which
  pod is actually holding this" - and release is a `WATCH`/`MULTI`
  compare-and-delete (get the value, only commit the `DELETE` if it hasn't
  changed), not a plain `GET`-then-`DEL`, so a stale release can't delete a
  lock someone else has since legitimately acquired. `conftest.py`'s
  `_fake_redis` fixture fakes both modules' clients separately with
  `fakeredis.FakeStrictRedis()` for the test suite.
- **`/help/` is reference documentation, not an onboarding tour.** It's
  reachable any time from the nav (`family.views.HelpView`,
  `family/templates/family/help.html`), never shown automatically on
  first login, and login-required only (not `FamilyRequiredMixin`) so
  it's usable before someone has created or joined their first family.
  The owner/editor section is gated the same `request.family_role`
  check used everywhere else in the app.
- **A required field's label gets a `*` marker - `{% field_label form.x %}`
  (`family.templatetags.form_extras`), not a bare `{{ form.x.label_tag }}`.**
  None of this app's hand-written form templates render fields through
  Django's own `{{ form }}` auto-rendering (which would apply
  `required_css_class` for free), so every one of them calls this
  instead wherever a field might be required -
  `person_form.html`/`union_form.html`/`create_family.html`/
  `family_settings.html`/`broadcast_form.html`. It's a drop-in
  replacement (`field.field.required` decides whether the marker shows;
  an optional field renders identically to plain `label_tag`) and lives
  under `family` for the same reason as several other cross-app template
  tags in this codebase (`notifications_extras`'s `absolute_static`/
  `absolute_page_url`) - a generic template-rendering utility, not
  something with real subject-matter ownership, loaded cross-app by
  whichever template needs it (`{% load form_extras %}`) same as those.
  A form with zero required fields (`FamilySenderSettingsForm` -
  `sms_sender_id`/`reply_to_email` are both optional) skips the legend
  line entirely rather than showing a pointless "nothing here is
  required" callout; `create_family.html`'s own "Family name" field is
  hand-written raw HTML (not a bound form field - `CreateFamilyView`
  handles `name` as a separate POST param from `FamilySenderSettingsForm`)
  so it carries the same marker by hand instead. The marker is a visual
  aid for a sighted user scanning the form before submitting - Django's
  own `required` HTML attribute already covers native browser validation
  and screen readers regardless of whether this marker is present.
- **Every button that mutates family data gets a `title` attribute**
  explaining what it does, in plain language naming the actual person/
  union/setting affected where relevant (e.g. "Remove Sheya Bernstein
  from the family", not just "Remove"). Native tooltips, not a custom
  component — deliberately simple for now; a styled tooltip would need
  its own CSS/JS added once for the whole app, worth doing later only if
  it's actually wanted. Purely navigational controls (Cancel, View
  family tree, the horizontal/vertical toggle, Sign out) don't need one.
  User-facing copy never says "ledger" — see the terminology bullet
  below for what replaced it.
- **"Family" and "workspace" mean different things in user-facing copy,
  on purpose.** A *family* is a user's actual relatives — the people in
  their tree. A *workspace* is the tenant boundary (the `Family` model,
  in the `tenants` app internally — that internal naming doesn't change,
  only what's shown on screen) that one family's tree, dates, and
  notifications live in; someone genuinely part of two families (born
  into one, married into another) has two workspaces to switch between.
  Before this distinction, both concepts were called "family," which
  reads fine most of the time (a workspace is usually named after the
  real family it holds) but breaks down exactly when explaining
  multi-workspace membership - "switching between your families" sounds
  like your relatives change, not which tree you're looking at. Nav/copy
  reflects this: "Workspace Settings", "Switch Workspace", "Name your
  workspace" / "Create workspace" (`tenants` app templates), all
  distinct from "family" wherever the real people are meant (the People
  list, "add someone to the family", `help.html`'s "Getting started"
  section). The app's own brand name is "Family Tree" (not "Ledger").
  Anything about workspaces or switching between them (the nav's own
  "Switch Workspace" link, `help.html`'s "Workspaces" section) only shows
  for an account that's actually part of more than one
  (`request.has_multiple_families`) or that holds the `tenants.
  add_family` permission - see the next bullet for why that specific
  permission, not multi-workspace membership, is what gates it for
  everyone else.
- **There's no self-service way to create a new workspace - only a site
  admin can.** A "site admin" is this app's own term for an account a
  platform operator has explicitly granted the `tenants.add_family`
  permission (in practice, Django staff via the admin) - not a family
  role (owner/editor/member), and not tied to any one family; see
  `help.html`'s own "Site admins" section (owner/editor-visible) for the
  non-technical explanation shown to actual users. `tenants.
  CreateFamilyView` enforces this directly (`PermissionRequiredMixin`,
  `permission_required = ["tenants.add_family"]`); this is deliberate for
  now (the platform operator wants to control onboarding), not a
  half-built feature - self-service creation is a plausible future
  addition once there's a billing story behind it, but that's out of
  scope today. The one place this needs care is `tenants.mixins.
  FamilyRequiredMixin`: an account with zero family memberships used to
  always redirect to `create_family`, but a permission-less account
  hitting that now would just trade one dead end for a worse one (a bare
  403 from `CreateFamilyView`, unexplained). It now checks `request.user.
  has_perm("tenants.add_family")` first - true still goes to
  `create_family`, false goes to `tenants:no_family_access`, a plain
  login-required (not `FamilyRequiredMixin` - that would loop right back
  here) page explaining that a site admin needs to set them up, not an
  error. `SwitchFamilyView` makes the same split for the same reason, for
  anyone who lands there directly with zero memberships.
- **Logging an exception means passing `exc_info`, always as the instance
  (`exc_info=exc`), never `exc_info=True`.** `config/logging_config.py`'s
  `render_exception` processor (in both the structlog chain and the
  stdlib `foreign_pre_chain` Django/Celery's own logs also flow through)
  turns that into a nested `exception: {type, message, file, line, stack}`
  object - `file`/`line` are the raise site, `stack` is the full call
  chain (file/line/function per frame, oldest to newest), deliberately
  with no source text and no local variables. That ruled out both of
  structlog's own built-in options: `format_exc_info` renders a single
  flat string (hard to filter/query), and `dict_tracebacks` dumps every
  frame's local variables (a real leak risk here - a frame's locals
  routinely include a message body, a token, or a destination address).
  Passing the exception instance rather than `True` means this still
  works even when the exception is logged well after its `except` block
  closed, since the instance carries its own `__traceback__` -
  `docker/scripts/wait_for_postgres.py` relies on exactly this, catching
  a connection error, returning it as a value, and only logging it
  several retries (and `sleep()` calls) later. `exc_info=True`/a raw
  `sys.exc_info()` tuple is still handled too, since that's the form
  Django's own exception logging uses, not something call sites here
  should reach for. Don't also pass a separate `error=str(exc)` field -
  it'd only duplicate `exception.message`.
- **A view/form/widget lives in the app that owns the model it's mainly
  about, not wherever it was first written.** Broadcast's views/form/
  widget (`notifications.views.BroadcastListView` and friends,
  `notifications.forms.BroadcastForm`, `notifications.widgets.
  TrixEditorWidget`), the notification-preference UI (`notifications.
  views.SubscriptionsView`/`UpdateEventTypePreferenceView`/
  `TogglePersonPreferenceView`), and the account-settings self-service
  form (`accounts.forms.AccountContactForm`, `accounts.views.
  UpdateAccountSettingsView`) all originally lived in `family` (that's
  where the request first landed, from a `family` app URL) even though
  none of them touch `Person`/`Union` as their main subject - `Broadcast`,
  `NotificationPreference`, and `Account` do. Moving them doesn't move
  their URLs: `notifications.urls` and `family.urls` are both mounted at
  the site root in `config/urls.py`, so a urlpattern can move from one
  app's `urls.py` to the other's - or point at a view living in a third
  app entirely, as `notifications.urls`'s own `update_notification_settings`
  route does for `accounts.views.UpdateAccountSettingsView` - without
  changing the path a browser or a test ever requests, only which
  `app_name:name` a `{% url %}`/`reverse()` call uses. `PersonDetailView`,
  `DashboardView`, and `GregorianToHebrewView` stay in `family` on
  purpose despite touching notification data too - they're genuinely
  Person/dashboard-centric pages that happen to render some notification
  state inline, not notification features that happen to live under a
  `family` URL.
- **Reusable, model-agnostic logic that would otherwise get duplicated
  across views/tasks lives in that app's `helpers.py`.**
  `notifications/helpers.py` has two: `channel_rows` (the per-channel
  subscribed/reason row-building loop `family.views.PersonDetailView`
  needed once for a person's own events and once for their unions' -
  originally copy-pasted, now called twice) and `html_to_plain_text`
  (the strip-tags-and-collapse-blank-lines step `_render_occurrence_
  message`/`_render_broadcast_message` in `notifications/tasks.py` and
  `accounts.views.RequestMagicLinkView` all need for their own
  plain-text fallback - cross-app reuse via a plain import, same as
  `accounts` already does for `notifications.services.send_email`/
  `send_sms`, not a reason to relocate the function). A function only
  moves into `helpers.py` once something *else* needs it too - don't
  pre-emptively extract a single call site's own logic into a helper
  just because it looks reusable. **It strips `<style>`/`<script>`
  blocks out *before* running `strip_tags`, not after** - `strip_tags`
  only ever removes tag *markup*, never a tag's own contents (documented
  Django behavior, not a bug in it), so the `<head><style>...</style>`
  block every email template shares via `templates/email/_base.html`
  used to leak its raw CSS text straight into the plain-text fallback
  verbatim - a real bug that shipped silently in every occurrence/
  broadcast email's `Message.body` from the HTML-template rewrite
  onward, only actually noticed once the sign-in email (below) started
  going through the same function too.

- **Every non-string env var is parsed through `config.helpers`, never a
  one-off `os.getenv(...)` call with ad hoc truthy/int logic inline.**
  `get_env_bool`/`get_env_int`/`get_env_list` (`config/settings.py`'s
  `DEBUG`/`DIASPORA`/`EMAIL_USE_TLS`, `ALLOWED_HOSTS`,
  `POSTGRES_PORT`/`EMAIL_PORT`; `docker/scripts/wait_for_postgres.py`'s
  `WAIT_FOR_POSTGRES_MAX_TRIES`/`SLEEP_BETWEEN`) all treat unset *or
  blank* the same way - fall back to `default` - and an unparseable int
  falls back to `default` too rather than raising and crashing settings
  import. **`get_env_bool` deliberately isn't `val.lower() in
  _TRUE_VALUES or default`** - that shape was a real, live bug: with
  `default=True` (`EMAIL_USE_TLS`, `DIASPORA`), an explicit falsy value
  in `.env` (`EMAIL_USE_TLS=False`) still isn't in the truthy set, so
  `or default` substituted `True` right back in - `EMAIL_USE_TLS` was
  silently stuck `True` no matter what `.env` said, for as long as the
  helper had this shape. The fix checks unset/blank explicitly first and
  returns `default` only there; any other value is judged strictly
  against `_TRUE_VALUES`, so an explicit `False` reliably beats a `True`
  default. `get_env_list`'s own default arg is `list[str] | None = None`
  or an empty list only ever built inside the function, not
  `default: list = []` - the classic shared-mutable-default trap - even
  though every current call site's default is either `None` or a
  literal built fresh at the call site anyway.

## Where to look next

Read the Django app docstrings and model comments for the actual mechanics
(`family/hebrew.py` for the calendar math, `family/access.py` for the
cross-tenant visibility rule, `notifications/tasks.py` for the scheduling
pipeline, `family/forms.PersonForm` for account provisioning,
`family/widgets.py` for the father/mother/spouse picker's own rendering
logic). This file is
meant to orient a new contributor or agent to *why* the project exists, not
to double as its architecture reference.

## Python

This project uses Python 3.13 with Poetry; use `poetry run ...`

Django (web) and Celery (worker) run in docker containers with local src mounted
into their containers. See `docker-compose.yml` and `docker-compose.dev.yml`

Local dev sends real (fake) email over SMTP to Mailpit (`docker-compose.dev.yml`)
rather than the plain `console.EmailBackend` - `.env`'s `EMAIL_HOST=mailpit`/
`EMAIL_PORT=1025` point at it, and sent mail (subject/body/links, as an actual
rendered message) is browsable at `http://localhost:8025`, not just dumped as
text into `docker logs`. `SMS_BACKEND=console` still just logs, since there's
no equivalent lightweight local SMS-catcher in play here.

## Docstrings

Any docstring we write uses [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
- a one-line summary, a blank line, then an extended description if needed,
then `Args:`/`Returns:`/`Raises:` sections once there's something in the
signature worth documenting:

```python
def resolve_send_date(anchor_date: date, *, notify_days_before: int) -> tuple[date, bool]:
    """Walks a notification date backward across Shabbat/Yom Tov.

    Only ever shifts earlier, never later - nobody's expected to check
    their phone on the day itself once it's already begun. See
    family/hebrew.py's own module docstring for why this can't just be
    "the day before" every time.

    Args:
        anchor_date: The Hebrew-calendar date the event actually falls on.
        notify_days_before: How many days ahead of anchor_date to start
            counting back from - 0 for a birthday/yahrzeit, a few for a
            Wedding reminder.

    Returns:
        A tuple of the resolved send date and whether it was actually
        shifted from the naive notify_days_before subtraction.
    """
```

This does **not** make docstrings mandatory everywhere - that's unchanged
from this file's existing rule (see the top-level style guidance): a
function whose name and type hints already say everything worth saying
(`__str__`, a one-line `clean()`, a Django CBV override with no surprises)
still gets none, same as before. Google style only governs the *shape* of a
docstring once one exists, and applies to two things:
- **Structure**: summary line first (imperative or descriptive, reads fine
  standalone in an IDE tooltip), a blank line, then prose - never `Args:`
  crammed onto the same paragraph as the summary, never a return value
  described in the middle of an unrelated sentence.
- **Args:/Returns:/Raises: only when the signature has something to say**.
  A single obvious parameter whose type hint already carries the meaning
  (`compute_occurrences_for_person(person: Person)`) doesn't need an
  `Args:` section spelling out "person: the person" - that's noise, not
  documentation. Add one once there's more than one parameter, an
  ambiguous one (a bare `bool`/`int` whose meaning isn't in its name), or a
  return value/tuple whose shape isn't obvious from the return type alone.
  This project already writes long narrative docstrings explaining *why*
  something exists or a non-obvious edge case (see this file's own
  examples throughout) - that narrative is the extended-description
  paragraph, not something Google style replaces. Args:/Returns: are
  additive on top of it, not a replacement for the prose.
- Don't restate a type hint's own type inside `Args:`/`Returns:` (no
  `person (Person): ...`) - the signature already has it; describe what
  the value *means*, not what type it is.
- **Narrative docstrings explaining *why* are welcome here, but keep each
  one to the point** - one or two sentences of real reasoning, not a
  multi-paragraph essay. If several new pieces of code in one change
  share the same underlying reasoning, say it in full once and reference
  it briefly from the others ("see X's own docstring") rather than
  re-explaining it every time it comes up.

Enforced by ruff's `D` rules (`pyproject.toml`,
`[tool.ruff.lint.pydocstyle]` `convention = "google"`) for anything that
already has a docstring - missing-docstring rules (`D100`-`D107`) are
explicitly off, matching the "not mandatory everywhere" rule above.

## Type hints

Every function/method we write (not Django/Celery's own base-class
signatures, which we only annotate on the override, not redesign) gets
param and return type hints - enforced by ruff's `ANN` rules
(`pyproject.toml`), which fail lint on a missing one. Prefer keyword
arguments at call sites for our own multi-argument functions, especially
where positional args could be confused for each other; a single obvious
argument (`compute_occurrences_for_person(person)`) doesn't need to be
forced into a keyword. `*/tests/*`, `conftest.py`, and `*/migrations/*`
are exempted - a test's signature is never read by anything, and
migrations are generated.

**`Any` is a last resort, not a default.** It satisfies the linter without
adding any real information, so reach for a concrete type first:

- Overriding a Django/Celery method whose own signature isn't typed
  (`form_valid`, `dispatch`, `get_context_data`, `Model.save`, a Celery
  signal receiver's `**kwargs`, ...) doesn't justify `Any` on the
  parameters that just forward to `super()` - annotate what's actually
  known (`request: HttpRequest`, `form: PersonForm` when a concrete form
  class is in play) and leave bare, unannotated `*args`/`**kwargs` for
  the rest. `ANN002`/`ANN003` are deliberately off in `pyproject.toml` for
  exactly this reason - a passthrough vararg forwarding to an untyped
  `super()` call gains nothing from `Any`, so it isn't forced to have one.
- `dict[str, Any]` is the right call for genuinely heterogeneous data a
  Django context dict, a parsed row from an ad hoc import, structlog's
  `event_dict` - where a value's real type varies per key and there's no
  useful narrower type to give it.
- If a third-party library's own type stubs define something as `Any`
  (e.g. `structlog.typing.WrappedLogger`), use that alias instead of a
  bare `Any` - same runtime meaning, but it names *why* it's dynamic
  instead of just giving up on the type.

## Test layout

Tests live next to the code they test, one `tests/` package per Django
app (`family/tests/`, `notifications/tests/`, `tenants/tests/`,
`accounts/tests/`), each with a `test_*.py` per source module -
`family/tests/test_views.py` tests `family/views.py`,
`notifications/tests/test_tasks.py` tests `notifications/tasks.py`, and
so on. There's no longer a catch-all top-level `tests/` directory; only
the root `conftest.py` lives at the project root.

**Tests are plain functions, never `unittest.TestCase`/class-based
groupings** - a `self`-taking method gains nothing here (no shared
`setUp`, no inherited assertions) over a fixture, and grouping by class
just adds an extra name to keep in sync with what the tests actually
cover. Where a class boundary used to provide context a flat function
name would lose (which underlying function/concern a group of tests is
about), that context moves into the function's own name (a shared
prefix, e.g. `test_broadcast_audience_...` for everything exercising
`resolve_broadcast_audience`) or into a plain comment above the group -
not into a class that exists only to hold `self.`-less test methods.
A `pytest.fixture`-returning helper method that multiple tests in a
former class shared (e.g. `TestBroadcastMute._broadcast_event_type`)
becomes a plain module-level function instead.

Fixture placement follows the same locality rule as the code: a fixture
used by only one test module is defined in that module; a fixture shared
by more than one test module *within the same app* goes in that app's
`tests/conftest.py` (e.g. `notifications/tests/conftest.py`'s
`birthday_event_type`); a fixture shared *across app boundaries* (e.g.
`family` and `two_families`, used by family/notifications/tenants tests
alike) goes in the root `conftest.py`. Don't hoist a fixture higher than
its actual usage requires - a single-module fixture doesn't belong in an
app conftest just because it might be reused someday.

A test that only reproduces on a specific real-world date (a Hebrew
calendar edge case, say) freezes "today" with `freezegun`'s
`freeze_time(...)` rather than being written to only pass when the suite
happens to run on that date - see
`notifications/tests/test_tasks.py::test_compute_occurrences_produces_a_
past_send_date_when_the_anchor_falls_on_yom_tov` for the pattern (frozen
to an actual Rosh Hashanah to reproduce a real bug end-to-end,
deterministically, on any day the suite runs).

## Parametrized tests

When several test functions call the same code path with the same
assertion shape and differ only in input/expected values, collapse them
into one `pytest.mark.parametrize`-decorated test rather than keeping
near-duplicate functions. This repo's convention for the decorator's
shape, consistently:

```python
@pytest.mark.parametrize(
    ["anchor_month", "adar_observance", "target_year", "expected_month"],
    [
        [Months.ADAR, "adar_ii", 5787, Months.ADAR_II],
        [Months.ADAR, "adar_i", 5787, Months.ADAR_I],
        [Months.ADAR_II, "adar_ii", 5788, Months.ADAR],
    ],
    ids=[
        "adar defaults to adar ii in a leap year",
        "adar respects adar i when configured",
        "adar ii collapses to adar in a non-leap year",
    ],
)
def test_adar_yahrzeit_observance(anchor_month, adar_observance, target_year, expected_month):
    ...
```

- `argnames` is a list of plain strings — not pytest's comma-separated-
  string shorthand (`"a,b"`).
- `argvalues` is a list of lists — not tuples.
- `ids` is always given explicitly, as lowercase natural-language
  phrases describing what's distinct about that case ("adar respects
  adar i when configured") — never left to pytest's auto-generated IDs
  (`Months.ADAR-adar_i-5787-Months.ADAR_I`) and never `snake_case`.
  Whatever used to be encoded in each separate function's name belongs
  in its `ids` entry instead, since the merged function's own name no
  longer carries that distinction.

Don't force-fit this: two tests that happen to be topically related but
need materially different setup or assert different *shapes* of thing
stay as separate functions. Parametrize is for genuine input/output
duplication, not a way to avoid having multiple test functions.

## Running lint and tests

Both run directly on the host, not via `docker compose run` — `poetry run
ruff check .`, `poetry run black --check .`, `poetry run pytest -q`. The
test suite runs against `config.settings_test` (sqlite in-memory DB, Celery
in eager mode, and an autouse fixture in the root `conftest.py` swapping in
`fakeredis`), so it needs no docker services up at all; nothing in this app
depends on a Postgres-specific feature, so sqlite is a faithful stand-in
here. Docker/docker-compose is for running the actual app (web/worker) and
its real Postgres/Redis, not for the dev feedback loop.

`pyproject.toml`'s `[tool.pytest.ini_options]` sets `DEBUG=True` for the
suite via `pytest-env`'s `env` ini-list (not a separate `[tool.pytest_env]`
table - that's an older config format the installed version doesn't read at
all, and fails silently rather than erroring, which is exactly how this got
missed once already). That alone isn't enough, though: pytest-django's own
`django_debug_mode` ini option defaults to forcing `settings.DEBUG = False`
regardless of what the settings module/pytest-env set it to (mirroring
`manage.py test`'s own behavior) - `django_debug_mode = "keep"` stops it
from overriding, so the `env` line actually takes effect. Without both
pieces, `DEBUG` silently stays `False` in tests even though `.env`'s own
`DEBUG=True` would suggest otherwise - which is also why the WhiteNoise
"No directory at: staticfiles/" warning used to show up in every test run
needing a request/response cycle: `WhiteNoiseMiddleware` sets its own
`autorefresh = settings.DEBUG`, and only checks/warns about `STATIC_ROOT`
existing when `autorefresh` is `False`.

## Static files

Only genuinely global assets live in the top-level `static/` dir -
`css/app.css` (loaded from `templates/base.html`, every page). Everything
else lives under its own app's `static/<app_name>/...` (Django's
`AppDirectoriesFinder` picks these up automatically, no `STATICFILES_DIRS`
entry needed) - `notifications/static/notifications/img/event-icons/`,
`notifications/static/notifications/js/broadcast_editor.js`, `family/static/
family/js/{hebrew_autofill,person_picker}.js`. An asset belongs to the app
its *subject matter* is about, not necessarily every app whose templates
happen to load it - `person_picker.js` lives under `family` (it's about
picking a Person) even though `notifications/templates/notifications/
broadcast_list.html`/`broadcast_form.html` load it too for the broadcast
people picker; a template in one app referencing another app's static file
by its full `app_name/...` path is normal and doesn't mean the asset is
misplaced. A new static asset used by exactly one app belongs under that
app's own `static/` dir from the start.

## Linting templates, JS, and CSS

Python isn't the only thing linted - Django templates and JS/CSS wherever
they live (top-level `static/` or an app's own `static/<app_name>/`) are
too, with tooling chosen to mirror ruff/black's split (a linter for
correctness, a formatter for style)
wherever a natural equivalent exists:

- **Templates** (`djLint`, `[tool.djlint]` in `pyproject.toml`): `poetry
  run djlint <template dirs>` lints, `poetry run djlint --reformat
  <template dirs>` formats (4-space indent, djLint's default - adopted
  as-is rather than fighting its opinions the way `H021` below is
  fought). `H021` ("inline styles should be avoided") is disabled
  project-wide - this app uses one-off inline `style="..."` attributes
  deliberately for layout tweaks that don't warrant a new CSS class, the
  same reasoning as ruff's own `E501` exception. `staticfiles/` (the
  `collectstatic` output directory - see `STATIC_ROOT` in
  `config/settings.py`) is excluded via `extend_exclude`, same as every
  other tool below - it's generated, not source, and would otherwise get
  linted as if it were a second copy of `static/`.
- **JS** (`eslint.config.js` + `.prettierignore`): ESLint's own
  `recommended` rules, plus `no-unused-vars` set to `vars: "local"` -
  each app's `static/<app>/js/*.js` (e.g. `family/static/family/js/`,
  `notifications/static/notifications/js/`) are plain `<script src>`
  files (no bundler), and
  their top-level functions (`initPersonPicker`, `initHebrewAutofill`)
  are entry points called from an inline `<script>` block in the
  template, not from anything ESLint can see; `"local"` limits the rule
  to variables in a nested scope, so a genuinely-unused local still gets
  caught. Prettier formats with its defaults (no `.prettierrc` - nothing
  about this app's existing JS style needed overriding).
- **CSS** (`.stylelintrc.json` + Prettier): `stylelint-config-standard`,
  with `declaration-block-single-line-max-declarations` disabled - this
  stylesheet deliberately keeps short one-off overrides
  (`.badge-birth { background: #e4ebe3; color: var(--sage-ink); }`) on a
  single line rather than expanding every two-property rule to three
  lines, the same "don't fight an intentional style" reasoning as
  djLint's `H021` above. Prettier still expands every rule onto separate
  lines when it formats, regardless of that stylelint exception -
  Prettier doesn't read stylelint's config, so `app.css`'s actual
  formatted shape is one-declaration-per-line throughout; the disabled
  stylelint rule only matters for anyone who runs stylelint without
  also running Prettier.

**Pre-commit hooks always auto-fix; CI always only checks - never the
reverse.** This app's Node tooling (`package.json`/`yarn.lock` -
`yarn install` once to set it up locally) backs both sides, so they run
the exact same installed tool versions and just pass different flags,
rather than each side pinning its own copy that could quietly drift
apart:

- **Pre-commit** (`.pre-commit-config.yaml`): every hook - `ruff --fix`,
  `black`, `djlint --reformat`/`djlint`, and `prettier`/`eslint --fix`/
  `stylelint --fix` - is `language: system`, calling straight into this
  repo's own installed copies (`poetry run djlint ...` for the Python
  ones, `yarn run ...` for the Node ones) instead of an isolated
  per-hook environment. djLint's own upstream pre-commit repo has no
  generic reformat hook at all (only lint-only profile hooks like
  `djlint-django`), which is why it's invoked directly rather than
  through a hook id that doesn't exist.
- **CI** (`.github/workflows/ci.yml`): `black --check`, `ruff check`,
  `djlint --check` (Python, direct via poetry) and `prettier --check`/
  `eslint`/`stylelint` (Node, via `yarn install --frozen-lockfile` then
  direct invocation - `eslint`/`stylelint` are check-only by default
  with no `--fix` passed; `prettier` needs `--check` explicitly, since
  its default behavior is to write). Never `pre-commit run` in CI - that
  would inherit the hooks' own auto-fix flags and silently rewrite files
  mid-job instead of just reporting. Each tool is its own CI step rather
  than one combined step, so a failure shows up against that specific
  tool's name in the checks list instead of being buried inside a
  single "lint everything" step.

## Git workflow

Every change goes through a feature branch and a pull request into
`main` - never a direct commit to `main`. `main` is what CI treats as
deployable (`publish` in `.github/workflows/ci.yml` builds and pushes an
image on every push to it), so it stays green by construction: nothing
lands there that hasn't gone through `lint`/`test` on its own branch
first.

Never push to git without asking the user first. Never merge a PR on
your own.

Git commit message types serve as a beacon of understanding in the sea of
changes that is a software project. These types not only categorize the
changes made but also communicate the intent and scope of each commit.

Here are the common types:
1. **feat**: Introducing new features or significant improvements.
2. **fix**: Bug fixes that resolve issues in your code.
3. **docs**: Updates or additions to documentation.
4. **style**: Cosmetic changes that don't affect code functionality (like formatting).
5. **refactor**: Code changes that neither fix a bug nor add a feature but improve structure.
6. **test**: Everything about testing - adding or fixing tests.
7. **chore**: Routine tasks or updates to the build process.
8. **perf**: Enhancements that improve performance.
9. **ci**: Modifications related to CI/CD processes.
10. **build**: Changes affecting the build system or external dependencies.
11. **revert**: Undoing previous changes.

**Commit messages are short and mechanical; the "why" goes in the PR
description, not the commit.** A commit message says what changed, in
one line (`fix: ...`/`feat: ...`/plain imperative, matching whatever this
repo's own history is already doing - check `git log` rather than
inventing a new convention). The PR description is where the actual
narrative lives: what problem this solves, why this approach over the
alternatives, what was verified and how. Splitting it this way keeps
`git log`/`git blame` scannable (a one-line summary per change, not a
paragraph to scroll past) while still keeping the reasoning somewhere
real - the PR - rather than losing it entirely. A PR bundling several
related commits doesn't need each commit to carry its own essay either;
one commit can be terse even when the PR as a whole represents a lot of
discussion and iteration to get there.

**PR titles follow the same `type: description` convention as commit
messages, and both are lowercase after the type prefix** - `fix: don't
say "Today is..." for a shifted occurrence`, not `Fix: Don't Say...` or
a bare `Model Meta cleanup`. One consistent format across `git log` and
the PR list means either one is scannable on its own, and a PR whose
title doesn't already start with a type prefix is a sign it's bundling
unrelated changes that should probably be split instead.
