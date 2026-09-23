# family — implementation notes

Loaded automatically when Claude Code reads files under `family/`.
Deep rationale for this app's own decisions - see the root `AGENTS.md`
for the project-wide orientation these build on.

## Calendar & scheduling

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

## Recompute & model state

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

## History & the family tree UI

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

## Forms, pickers, and widgets

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

## Misc UI conventions

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
