# Family Birthdays

A multi-tenant life-events tracker for extended families, built around the
Hebrew calendar. Each family gets its own private tree of births, deaths, and
marriages, and gets notified by email or SMS about birthdays, yahrzeits
(death anniversaries), and wedding anniversaries — on the correct Hebrew date,
every year.

## Why

Jewish life-cycle observances run on the Hebrew calendar, not the Gregorian
one, and getting that wrong isn't cosmetic — it means notifying someone on
the wrong day for a yahrzeit. This app treats the Hebrew date as the thing
that actually drives scheduling: every birth, death, and marriage is recorded
in both calendars, and the recurring anniversary is recomputed against the
Hebrew calendar every year, including leap-year Adar splits and short-month
edge cases. Notifications that would otherwise land on Shabbat or Yom Tov go
out the day before instead.

## Features

- **Multi-tenant families** — each family's tree, event types, and
  notification preferences are private to that family, with the one
  deliberate exception of an in-law reachable through a marriage.
- **Dual-calendar dates** — every date is stored in both Gregorian and
  Hebrew, and recurring events are computed against the Hebrew calendar.
- **Passwordless sign-in** — magic links over email or SMS, no passwords to
  manage. A person can belong to more than one family and switch between
  them.
- **Role-based access** — owners and editors can manage the tree; everyone
  can view it and manage their own notification subscriptions.
- **Email + SMS notifications** — birthdays, yahrzeits, anniversaries, and
  family-wide broadcasts, shifted off Shabbat/Yom Tov automatically.
- **Interactive family tree** — a rendered chart of the whole family, with
  birth-order-aware sibling sorting.
- **Field-level privacy** — a plain member can't see anyone else's birth
  year, only their own.

## Tech stack

Django 5, PostgreSQL, Redis, Celery (with RedBeat for scheduling), and the
[`hdate`](https://github.com/py-libhdate/py-libhdate) library for Hebrew
calendar math. Runs in Docker; static assets are served via WhiteNoise.

## Getting started

```bash
cp .env.sample .env
docker compose up --build
```

The app will be available at `http://localhost:8000`. In development, sent
emails are captured by [Mailpit](https://github.com/axllent/mailpit) at
`http://localhost:8025` instead of actually being delivered.

`.env.sample` ships with working local defaults — change `SECRET_KEY` and
the database/Redis passwords before deploying this anywhere beyond your own
machine.

## Development

This project uses [Poetry](https://python-poetry.org/) for Python
dependencies and [Yarn](https://yarnpkg.com/) for JS/CSS lint tooling.

```bash
poetry install
poetry run pytest
poetry run ruff check .
poetry run black --check .
yarn install
yarn lint
```

See [`AGENTS.md`](./AGENTS.md) for the project's conventions and the
reasoning behind them — it's written for anyone (human or AI) picking up
this codebase, not just for AI agents.

## License

[MIT](./LICENSE)
