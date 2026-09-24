# config — implementation notes

Loaded automatically when Claude Code reads files under `config/`.
Covers observability, infra/ops, and repo-wide code-organization
conventions (env var parsing, logging, where a view/form/widget
belongs) - see the root `AGENTS.md` for the project-wide orientation
these build on.

## URL-safe identifiers

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

## Task scheduling infra

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

## Site/network config

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

## Redis

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

## Logging

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

## Code organization conventions

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


## Env var parsing

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


## Observability

- **OpenTelemetry is the always-on, vendor-neutral source of truth; Tempo
  and Sentry are just exporters attached to the same `TracerProvider`, not
  independent instrumentation.** `config/observability/tracing.py` builds
  one `TracerProvider` per process (spans always recorded, regardless of
  whether `OTEL_ENABLED` attaches a real OTLP exporter) and instruments
  Django/Celery/Redis/psycopg2/botocore off it; `config/observability/
  sentry.py` mirrors those same spans into Sentry via `SentrySpanProcessor`
  rather than letting Sentry's own Django/Celery auto-instrumentation start
  a second, competing root span - see that module's own docstring for why
  this is what makes trace/span ids match across Tempo, Sentry, and the
  structured logs. `config/logging_config.py`'s `add_trace_context`
  processor is the piece that actually gets those ids into every JSON log
  line - without it the correlation the whole design exists for wouldn't
  reach the logs at all.
- **Neither `init_tracing()` nor Django/Celery instrumentation is called
  from `config/settings.py` itself.** `DjangoInstrumentor().instrument()`
  mutates `django.conf.settings.MIDDLEWARE`, which is exactly the kind of
  thing that's fragile to do from inside the settings module while it's
  still mid-import. `config/wsgi.py` calls `init_tracing()`;
  `config/celery.py`'s `worker_process_init` receiver calls it again,
  post-fork, in each Celery prefork child - a `BatchSpanProcessor`'s
  background export thread doesn't survive `fork()` correctly, so the
  parent process's own initialization isn't enough for Celery the way it
  is for gunicorn (which forks *before* `config.wsgi` is even imported, so
  it never needs a second call). `init_tracing()` is idempotent, so
  calling it more than once in one process is a no-op past the first
  call. A bare `manage.py` command never calls this at all - a one-off
  command doesn't need request/task tracing.
  **`init_tracing()` runs *before* `get_wsgi_application()` in
  `config/wsgi.py`, not after - found the hard way, by tracing a real
  request end-to-end and finding no request span at all, only an
  orphaned, parent-less `SELECT` span from psycopg2's own independent
  instrumentation.** `get_wsgi_application()`'s own `WSGIHandler.__init__`
  is what compiles `settings.MIDDLEWARE` into the actual request-handling
  chain (`load_middleware()`) - calling `DjangoInstrumentor().instrument()`
  any later means its middleware insertion mutates a list nobody reads
  again, so the request span it's meant to create silently never gets
  built, even though `settings.MIDDLEWARE` itself looks correct on
  inspection (the entry's right there in the list - it's just wired to a
  `WSGIHandler` instance that was already built before the mutation
  happened). `django.conf.settings` is still safely readable at this
  point without `get_wsgi_application()` having run first - accessing it
  (which `DjangoInstrumentor` needs, to insert into `MIDDLEWARE`) fully
  executes `config/settings.py` on first attribute access regardless.
- **Prometheus metrics are served from a dedicated port-9090 process per
  container, separate from the main app port, using `prometheus_client`'s
  multiprocess mode** - both gunicorn's own worker prefork and Celery's
  worker prefork pool need per-pid metric files aggregated across
  processes (`config/observability/multiproc.py`, ported from a FastAPI
  service built the same way). `docker/entrypoints/_run_with_metrics.sh`
  is what `web.sh`/`worker.sh` now `exec` into instead of the main command
  directly - it starts the metrics gunicorn as a background sibling, then
  `exec`s into the real command so the main app *becomes* PID 1, rather
  than staying a wrapper script forever. An earlier version kept the
  wrapper script as PID 1 and used a `trap`+background-PIDs+bare `wait` to
  tear both processes down together - wrong, since a bare `wait` only
  returns once *every* backgrounded job has exited: the main app crashing
  while the metrics sidecar stayed up never ended the container, silently
  defeating `restart: unless-stopped`. The `exec`-based version fixes
  that, but hands PID 1's reaping duties to whatever occupies that slot
  afterward - the metrics sidecar ends up parented to the main app once
  the wrapper's own image is replaced, and nothing about gunicorn's or
  Celery's own arbiter loop guarantees it calls `wait()` on a child it
  never forked itself, which is exactly the accumulating-zombie problem a
  real PID-1 init is for. **This is why the Dockerfile runs `tini` as the
  actual `ENTRYPOINT`** (`apk add tini`, Alpine ships it) rather than
  relying on the wrapper script to reimplement generic child-reaping by
  hand. **`GUNICORN_CMD_ARGS` (the `--control-socket` path) is set at each
  gunicorn call site, not as one container-wide Dockerfile `ENV`** -
  `web.sh` sets its own for the main app's gunicorn, `_run_with_metrics.sh`
  sets a different one (scoped to just that command) for the metrics
  gunicorn, since both processes sharing one path would mean two masters
  racing to bind the same socket file. Leaving it empty/unset doesn't
  disable the control socket feature either - gunicorn just falls back to
  its own CWD-relative default (`/app/.gunicorn/...`), which fails
  outright under `docker-compose.dev.yml`'s bind-mounted `/app` (that
  mount doesn't support UNIX domain sockets) - found the hard way, as a
  real startup error, not a hypothetical. Gunicorn workers get their own per-pid setup
  via `config/gunicorn_conf.py`'s `post_fork` hook (that file still can't
  import `config.settings` - see its own docstring - which is why the
  hook imports `config.observability.multiproc` directly instead, a
  module with no Django dependency at all).
- **Every notification-volume env var/setting lives in `config/settings.py`
  alongside everything else this app parses** (`OTEL_*`, `SENTRY_*`,
  `METRICS_NAMESPACE`, `PROMETHEUS_MULTIPROC_DIR`, all through
  `config/helpers.py`, including a new `get_env_float` for
  `OTEL_TRACES_SAMPLE_RATE`) - `config/observability/*` modules read them
  via `django.conf.settings`, never a second, parallel `os.getenv()` call
  of their own, the one deliberate exception being
  `PROMETHEUS_MULTIPROC_DIR` itself: `config/observability/multiproc.py`
  reads that one straight from `os.environ`, because `prometheus_client`'s
  multiprocess mode needs the literal process environment variable set
  (not just a Django setting) to even switch into multiprocess mode in the
  first place, and the Dockerfile's own `ENV PROMETHEUS_MULTIPROC_DIR=...`
  already guarantees it's there before any Python process starts.
- **`notifications_emails_sent_total`/`notifications_sms_sent_total`
  (email/SMS volume, for cost visibility over time - SMS by destination
  country) are incremented inside `notifications.services.send_email`/
  `send_sms` themselves, not `notifications.tasks.send_message`.** The
  magic-link sign-in flow (`accounts.views.RequestMagicLinkView`) calls
  `send_email`/`send_sms` directly, bypassing `Message`/`send_message`
  entirely (see the Person/Account bullet elsewhere in this file) -
  instrumenting only `send_message` would silently miss every sign-in
  email/SMS. Both functions take a required keyword-only `event_type: str`
  - never the raw, family-controlled `EventType.code` (a family can set
  that to anything), always one of the 7 `EventType.BuiltinCode` values,
  `"custom"` for a family-defined event type, or `"magic_link"` - see
  `notifications.tasks._metric_event_type` for the mapping. Country is
  derived from the E.164 `to` number via `phonenumbers.
  region_code_for_number` (`notifications.services.
  _country_for_sms_metric`), falling back to `"unknown"` rather than
  raising - a metric label is never worth blocking a real send over.
- **SES/SNS $-cost math deliberately isn't a Prometheus recording rule or
  anything in app code - it lives entirely in Grafana's own dashboard
  JSON** (the "SMS volume by country" panel, via a `calculateField`
  transform). Prometheus/the app only ever emit pure volume counters.
  SNS/SES pricing changes independently of this app's own state and
  varies by destination country in ways nothing here should need a
  deploy to reflect - a price update is a dashboard-panel edit, which
  re-evaluates the whole historical time range correctly the moment it's
  changed, unlike a value baked into a metric at write time.
- **`beat_task_last_success_timestamp` is set by hand, inside
  `compute_occurrences`/`send_due_notifications`/`send_due_broadcasts`
  themselves, at the very end of each function body - never derived from
  a generic Celery signal.** These three tasks are *designed* to silently
  self-heal (idempotent recompute, `send_date__lte`/`send_at__lte`
  catch-up, RedBeat's own leader lock - see the Beat bullet elsewhere in
  this file), so "Celery ran this task" isn't the same claim as "this task
  did its job" - a worker restart missing one `send_due_broadcasts` tick
  and catching up on the next run is working exactly as designed, not an
  incident worth a stale-gauge alert. A signal-based "last ran" gauge
  can't tell that apart from a genuine failure; a gauge set at the end of
  a function body that actually completed naturally can.
