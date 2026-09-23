# tenants — implementation notes

Loaded automatically when Claude Code reads files under `tenants/`.
Deep rationale for this app's own decisions - see the root `AGENTS.md`
for the project-wide orientation these build on.

## Permissions & tenant boundary

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

## Infra

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

## Workspace terminology & self-service

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
