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
Notifications that would otherwise land on Shabbos or Yom Tov go out the day
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

Each bullet below is a one-line pointer, not the full story - the actual
reasoning (the "found for real" bugs, the alternatives considered, the
exact code paths) now lives in the nested `AGENTS.md` file for the app
it's about, loaded automatically by Claude Code when you're working in
that app's directory. See "Where to look next" below for the full list.

- **A family is a tenant.** Each family's data is isolated, except an
  in-law reachable through a marriage is visible to both sides. (`tenants/AGENTS.md`)
- **Permissions resolve in one place**, `tenants.permissions.resolve_family_permissions`,
  never re-derived from `request.family_role` inline. (`tenants/AGENTS.md`)
- **A role check isn't a tenant check** - `FamilyScopedMixin` separately
  guarantees a URL's record actually belongs to the viewer's family. (`tenants/AGENTS.md`)
- **The two calendars (Hebrew/Gregorian) are recorded independently, never derived** -
  the conversion is ambiguous around sunset, so only a human can supply both. (`family/AGENTS.md`)
- **A displayed Hebrew year always drops the thousands digit** (`תשפ"ו`, not `ה' תשפ"ו`). (`family/AGENTS.md`)
- **Life events are user-defined, but Birthday/Yahrzeit/Anniversary/Bar-Bat-Mitzvah
  come free**, each with its own recurrence and lead-time rules. (`notifications/AGENTS.md`)
- **A computed `send_date` can legitimately land in the past** (a Shabbos/Yom-Tov
  shift landing on Yom Tov itself) - every consumer treats that as normal. (`notifications/AGENTS.md`)
- **Occurrence recompute is a `post_save` signal**, not a call sprinkled
  across every view that touches a Person/Union. (`family/AGENTS.md`)
- **There's no "engaged" status** - a Union is `MARRIED` from creation;
  `Union.is_upcoming` (computed) is what "not married yet" means. (`family/AGENTS.md`)
- **A stale subscription toggle is hidden, not left wrong** - Birthday/Bar-Mitzvah
  stop once not living, Anniversary stops once either spouse has died. (`family/AGENTS.md`)
- **`Union.Status.WIDOWED`/`DIVORCED` are a second, independent path** to the
  same event suppression, for when a death isn't recorded yet. (`family/AGENTS.md`)
- **A "tracked" person is one with `notifications_enabled=True`** - untracked
  people are lineage-only stubs, hidden from the People list, and never get
  events computed for them at all. (`family/AGENTS.md`)
- **Notification preferences resolve most-specific-wins**: a person/union
  override beats an account's per-event-type setting, which beats the
  event type's own default. (`notifications/AGENTS.md`)
- **A Broadcast is a one-off free-text update**, sent directly rather than
  computed from an anchor date, with its own claim-then-send safety and
  pending-visibility rules. (`notifications/AGENTS.md`)
- **Upcoming/Broadcasts use a `.timeline` list**, not the compact `.ledger`
  list used for per-person subscription exceptions. (`notifications/AGENTS.md`)
- **Every event type gets its own template, per channel** - email is real
  HTML with a plain-text fallback derived from it; SMS is budget-truncated
  plain text with its own short template. (`notifications/AGENTS.md`)
- **A person record (Person) and a login (Account) are different things** -
  the same human can appear as a Person in more than one family's ledger
  but only ever has one Account. (`accounts/AGENTS.md`)
- **The magic-link sign-in email/SMS is the one message with no single
  family to brand from** - resolves to a family's branding only when the
  account belongs to exactly one. (`accounts/AGENTS.md`)
- **Migration history is squashed to one `0001_initial.py` per app**, done
  once before the first real commit - not a pattern to repeat casually.
- **Every meaningful edit is versioned** via django-reversion, surfaced on
  a person's own detail page as a diff-per-field History card. (`family/AGENTS.md`)
- **The family tree UI is a third-party library** (`family-chart`, D3-based) -
  don't rebuild layout/pan/zoom from scratch. (`family/AGENTS.md`)
- **Editing from the tree redirects to the real forms** - it never opens a
  second, parallel edit UI. (`family/AGENTS.md`)
- **Father/mother/spouse pickers are searchable, gender-filtered, and
  labeled with a birth year** - duplicate names are the norm in a ledger
  this size. (`family/AGENTS.md`)
- **The Tom Select dropdown renders a two-line option** (name + year,
  Hebrew name as an RTL subtitle) via `data-*` attributes Tom Select
  doesn't read natively. (`family/AGENTS.md`)
- **Every phone input gets the same intl-tel-input widget**; every email
  input gets the same assistive (never authoritative) validation. (`accounts/AGENTS.md`)
- **The broadcast people picker excludes untracked people** - they never
  get notified of anything, so there's nothing to pick them for. (`notifications/AGENTS.md`)
- **Hebrew date fields can be prefilled from the Gregorian one, but never
  authoritatively** - only fires when the Hebrew fields are still empty. (`family/AGENTS.md`)
- **Every model gets a `uuid` field** - any value crossing an HTTP boundary
  identifies a record by it, never by the integer pk. (`config/AGENTS.md`)
- **Beat runs embedded in the worker** (RedBeat, not `django_celery_beat`) -
  safe even if the worker is scaled to more than one replica. (`config/AGENTS.md`)
- **`SITE_BASE_URL` is one `proto://fqdn` setting**, not domain + a
  use-https flag, and not `django.contrib.sites`. (`config/AGENTS.md`)
- **Redis config is discrete env vars**, not one `REDIS_URL` - mirrors the
  `POSTGRES_*` pattern. (`config/AGENTS.md`)
- **`migrate_with_lock` talks to Redis directly**, not Django's cache
  framework - there's no distributed cache configured. (`tenants/AGENTS.md`)
- **`/help/` is reference documentation, not an onboarding tour** - reachable
  any time, never shown automatically. (`family/AGENTS.md`)
- **A required field's label gets a `*` marker** via `{% field_label %}`,
  not bare `label_tag` - none of this app's forms use Django's own
  auto-rendering. (`family/AGENTS.md`)
- **Every button that mutates family data gets a `title` attribute**
  explaining what it does in plain language. (`family/AGENTS.md`)
- **"Family" and "workspace" mean different things in user-facing copy** -
  a family is your actual relatives; a workspace is the tenant boundary
  that tree lives in. (`tenants/AGENTS.md`)
- **There's no self-service workspace creation** - only a "site admin"
  (a global Django permission, not a family role) can. (`tenants/AGENTS.md`)
- **Logging an exception passes `exc_info` as the instance**, never
  `exc_info=True` - lets a structured `exception: {...}` object render
  consistently, with no local variables leaked into logs. (`config/AGENTS.md`)
- **A view/form/widget lives in the app that owns the model it's mainly
  about**, not wherever it was first written - URLs don't have to move
  with it. (`config/AGENTS.md`)
- **Reusable, model-agnostic logic goes in that app's `helpers.py`**, once
  something *else* needs it too - not preemptively. (`config/AGENTS.md`)
- **Every non-string env var is parsed through `config.helpers`**, never
  a one-off `os.getenv()` with ad hoc truthy/int logic. (`config/AGENTS.md`)
- **OpenTelemetry is the always-on source of truth; Tempo and Sentry are
  just exporters** attached to the same `TracerProvider`. (`config/AGENTS.md`)
- **`notifications.tasks.send_message` retries with exponential backoff**
  via Celery's own `autoretry_for`, and `TaskPriority` gives sign-in
  messages a real priority queue over background sweeps. (`notifications/AGENTS.md`)
- **AWS SNS's 10 req/s account-wide limit is enforced with a Redis-backed
  global counter**, not Celery's own (per-worker-only) `rate_limit=`. (`notifications/AGENTS.md`)

## Where to look next

This file orients a new contributor or agent to *why* the project exists -
it's not the architecture reference. The full depth behind every `Core
ideas` bullet above (the "found for real" bugs, alternatives considered,
exact code paths) lives in a nested `AGENTS.md` per app instead - Claude
Code loads each one automatically the first time it reads a file under
that directory, so it costs nothing in a session that never touches that
app. Without equivalent nested-memory-file support, read the relevant one
directly:

- `tenants/AGENTS.md` - permissions, tenant boundary, workspace terminology
- `family/AGENTS.md` - calendar/scheduling, model state, tree UI, pickers/widgets
- `notifications/AGENTS.md` - event types, Broadcasts, message rendering, task scheduling
- `accounts/AGENTS.md` - Person/Account split, magic-link sign-in, phone/email widgets
- `config/AGENTS.md` - uuid/infra conventions, logging, code organization, observability

Also worth reading directly for the mechanics: `family/hebrew.py` (calendar
math), `family/access.py` (cross-tenant visibility), `notifications/tasks.py`
(scheduling pipeline).

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
    """Walks a notification date backward across Shabbos/Yom Tov.

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

Not mandatory everywhere: a function whose name and type hints already say
everything worth saying (`__str__`, a one-line `clean()`) still gets none.
Once one exists, Google style governs its *shape*, not whether to write
narrative: summary line, blank line, prose - never `Args:` crammed onto the
summary. Add `Args:`/`Returns:`/`Raises:` only when the signature has
something to say (a single obvious param doesn't need one; don't restate a
type hint's own type there - describe what the value *means*). Narrative
"why" prose is welcome but stays to a sentence or two per fact - if several
new pieces of code share the same reasoning, say it once and reference it
("see X's own docstring") rather than re-explaining every time.

Enforced by ruff's `D` rules (`[tool.ruff.lint.pydocstyle]`,
`convention = "google"`); `D100`-`D107` (missing-docstring) are off, matching
the "not mandatory everywhere" rule above.

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

**`Any` is a last resort, not a default** - it satisfies the linter without
adding real information. Overriding an untyped Django/Celery method
(`form_valid`, `dispatch`, `Model.save`) doesn't justify `Any` on params that
just forward to `super()` - annotate what's actually known, leave bare
`*args`/`**kwargs` for the rest (`ANN002`/`ANN003` are off for exactly this).
`dict[str, Any]` is right for genuinely heterogeneous data (a Django context
dict, structlog's `event_dict`); if a third-party lib's own stubs define
something as `Any` (e.g. `structlog.typing.WrappedLogger`), use that alias
instead of a bare one - same runtime meaning, names *why* it's dynamic.

## Dates

Every weekday name renders through Django's own date formatting
(`dateformat.format(date, "l"/"D")` or the `|date:"l"`/`|date:"D"` filter),
never `strftime`/`%A`/`%a`. `family.apps.FamilyConfig.ready()` patches
`dateformat.WEEKDAYS`/`WEEKDAYS_ABBR` so Saturday reads "Shabbos" - only
Django's own formatting machinery reads that patched dict, so `strftime`
would silently still say "Saturday."

## Enums

Every enum backing something persisted (a model field, a JSON-stored value)
uses `django.db.models.TextChoices`, always `NAME = "stable_value", "Friendly
Label"` - a lowercase, stable value distinct from the label, never the same
string doing both jobs. This includes the two standalone ones not nested in
a Model (`notifications.enums.ChannelEnum`/`ShiftReason` - needed by
`family.hebrew`/`accounts` without inverting the usual dependency direction)
- still real `TextChoices`, since a plain `django.db.models` import carries
no circular-import risk. `.label` is what a template shows; `.value` is
what's persisted (`Occurrence.shift_reasons` stores `["shabbos", "yom_tov"]`,
never the label text, so a wording tweak never needs a data migration).

A member's own `str()` is exactly its `.value` (documented Django behavior),
so it can be interpolated/serialized directly with no explicit `.value`
needed - `accounts.magic_links.issue_token`'s Redis-stored token payload
relies on exactly this.

## Test layout

Tests live next to the code they test, one `tests/` package per Django app,
each with a `test_*.py` per source module (`family/tests/test_views.py`
tests `family/views.py`). Only the root `conftest.py` lives at the project
root - no catch-all top-level `tests/` directory.

**Tests are plain functions, never `unittest.TestCase`/class-based
groupings** - a `self`-taking method gains nothing over a fixture, and a
class just adds a name to keep in sync. Context a class boundary would have
given moves into the function's own name (a shared prefix like
`test_broadcast_audience_...`) or a plain comment above the group. A fixture
used by only one test module is defined there; shared within an app, it
goes in that app's `tests/conftest.py`; shared across app boundaries, the
root `conftest.py`. Don't hoist a fixture higher than its actual usage
requires.

A test that only reproduces on a specific real-world date freezes "today"
with `freezegun`'s `freeze_time(...)` rather than being written to only
pass when the suite happens to run on that date.

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

- `argnames` is a list of plain strings, not pytest's `"a,b"` shorthand;
  `argvalues` is a list of lists, not tuples.
- `ids` is always given explicitly, as lowercase natural-language phrases
  ("adar respects adar i when configured") — never pytest's auto-generated
  IDs and never `snake_case`. Whatever used to be encoded in each separate
  function's name belongs in its `ids` entry instead.

Don't force-fit this: two tests that need materially different setup or
assert different *shapes* of thing stay as separate functions - parametrize
is for genuine input/output duplication, not to avoid multiple functions.

## Running lint and tests

Both run directly on the host, not via `docker compose run` — `poetry run
ruff check .`, `poetry run black --check .`, `poetry run pytest -q`. The
suite runs against `config.settings_test` (sqlite in-memory, Celery eager,
`fakeredis` autoused), so it needs no docker services up - nothing here
depends on a Postgres-specific feature. Docker is for running the actual
app, not the dev feedback loop.

`DEBUG=True` for the suite needs both `[tool.pytest.ini_options]`'s
`env = ["DEBUG=True"]` (`pytest-env`) *and* `django_debug_mode = "keep"` -
pytest-django otherwise forces `settings.DEBUG = False` regardless of what
`env` sets, mirroring `manage.py test`'s own behavior. See `pyproject.toml`'s
own comment there for what silently broke before both pieces were in place.

## Static files

Only genuinely global assets live in the top-level `static/` dir -
`css/app.css`. Everything else lives under its own app's
`static/<app_name>/...` (Django's `AppDirectoriesFinder` picks these up
automatically). An asset belongs to the app its *subject matter* is about,
not every app whose templates happen to load it - `person_picker.js` lives
under `family` even though `notifications/templates/...` loads it too for
the broadcast people picker; a template referencing another app's static
file by its full `app_name/...` path is normal, not a sign it's misplaced.

## Linting templates, JS, and CSS

Python isn't the only thing linted - templates and JS/CSS are too, tooling
chosen to mirror ruff/black's linter/formatter split:

- **Templates** (`djLint`, `[tool.djlint]`): `poetry run djlint <dirs>`
  lints, `--reformat` formats. `H021` ("inline styles should be avoided")
  is disabled project-wide - this app uses one-off inline `style="..."`
  deliberately for layout tweaks that don't warrant a new CSS class, same
  reasoning as ruff's own `E501` exception. `staticfiles/` (the
  `collectstatic` output) is excluded everywhere below - generated, not
  source.
- **JS** (`eslint.config.js` + `.prettierignore`): ESLint's `recommended`
  rules plus `no-unused-vars: vars: "local"` - see that config file's own
  comment for why (these files are plain `<script src>`, no bundler, so
  their entry-point functions aren't visible to ESLint as "used").
  Prettier formats with its defaults.
- **CSS** (`.stylelintrc.json` + Prettier): `stylelint-config-standard`,
  with `declaration-block-single-line-max-declarations` disabled - this
  stylesheet deliberately keeps short one-off overrides on a single line,
  same "don't fight an intentional style" reasoning as `H021` above.
  Prettier still expands every rule when it formats regardless (it
  doesn't read stylelint's config) - the disabled rule only matters for
  running stylelint standalone.

**Pre-commit hooks always auto-fix; CI always only checks - never the
reverse.** Both run the same installed tool versions (`package.json`/
`yarn.lock`), just different flags, so they can't quietly drift apart.
Pre-commit (`.pre-commit-config.yaml`) is all `language: system`, calling
straight into this repo's own installed copies. CI (`.github/workflows/
ci.yml`) never runs `pre-commit run` directly - that would inherit the
hooks' own auto-fix flags and silently rewrite files mid-job instead of
just reporting; each tool gets its own CI step instead, so a failure shows
up against that specific tool's name.

## Git workflow

Every change goes through a feature branch and a pull request into
`main` - never a direct commit to `main`. `main` is what CI treats as
deployable (`publish` in `.github/workflows/ci.yml` builds and pushes an
image on every push to it), so it stays green by construction: nothing
lands there that hasn't gone through `lint`/`test` on its own branch
first.

Never push to git without asking the user first. Never merge a PR on
your own.

Commit types: `feat`/`fix`/`docs`/`style`/`refactor`/`test`/`chore`/`perf`/
`ci`/`build`/`revert` - standard conventional-commits meanings.

**Commit messages are short and mechanical; the "why" goes in the PR
description, not the commit.** One line, `type: description`, lowercase
after the prefix (`fix: don't say "Today is..." for a shifted occurrence`,
not `Fix: Don't Say...`) - matching whatever this repo's own history is
already doing (check `git log`, don't invent a new convention). The PR
description carries the actual narrative: what problem this solves, why
this approach, what was verified. A PR bundling several commits doesn't
need each one to carry its own essay. PR titles follow the same convention
- one whose title doesn't start with a type prefix is a sign it's bundling
unrelated changes that should probably be split.
