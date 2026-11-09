# MyMoney Tracker

**Track Your Money. Reach Your Goals.**

A personal financial management web application for Uganda (UGX), built as
a modular Django monolith with a versioned REST API, ready for Android/iOS
clients later. See `DECISIONS.md` for architectural decisions,
`PROJECT_STATUS.md` for current build status, and `CLAUDE.md` for an
engineering-context overview of the codebase.

## Stack

- **Backend:** Python 3.13, Django 5.1, Django REST Framework
- **Database:** PostgreSQL 16 (never floats for money — all amounts are `DecimalField`/`NUMERIC`)
- **Frontend:** Django templates, HTMX, Alpine.js, Tailwind CSS (CDN), Chart.js
- **Infra:** Docker, Docker Compose, Gunicorn, Caddy (reverse proxy + automatic HTTPS), WhiteNoise (static files)
- **Tests:** Django/pytest test suite (see `tests/`)

## Local development (without Docker)

Requires Python 3.13+ and a local PostgreSQL 16 server.

```bash
python -m venv .venv
source .venv/Scripts/activate        # Windows (Git Bash). Use .venv\Scripts\activate.bat for cmd.exe
pip install -r requirements-dev.txt

cp .env.example .env                 # then edit DATABASE settings for your local Postgres
# Either set DATABASE_URL directly, e.g.:
#   DATABASE_URL=postgres://mymoney:yourpassword@localhost:5432/mymoney
# or create the role/db yourself first:
#   createuser mymoney --pwprompt
#   createdb mymoney -O mymoney

python manage.py migrate
python manage.py seed_categories
python manage.py createsuperuser
python manage.py runserver
```

Visit http://127.0.0.1:8000/.

## Docker startup (recommended)

```bash
cp .env.example .env                 # fill in DJANGO_SECRET_KEY, POSTGRES_PASSWORD, DOMAIN, etc.
docker compose build
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

The `web` container's entrypoint (`docker/entrypoint.sh`) automatically waits
for PostgreSQL, runs migrations, and seeds default categories on every start.

Visit the domain you set in `DOMAIN` (or `http://localhost` for a local
test run — Caddy will serve it over plain HTTP since it can't obtain a
real certificate for `localhost`).

## Database migrations

```bash
# Locally:
python manage.py makemigrations
python manage.py migrate

# In Docker:
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate
```

## Creating an administrator

```bash
python manage.py createsuperuser                      # local
docker compose exec web python manage.py createsuperuser   # Docker
```

The admin dashboard lives at `/admin-panel/` (staff-only); Django's built-in
admin is still available at `/admin/` for raw data inspection.

## Running tests

Tests run against SQLite (see `config/settings/test.py`) so they never
require a live PostgreSQL server or touch your dev/production database:

```bash
pip install -r requirements-dev.txt
pytest
```

## Database backup

```bash
# Locally:
python manage.py backup_db

# In Docker:
docker compose exec web python manage.py backup_db
```

Backups are written to `backups/mymoney_<timestamp>.sql`. This is also
available as a one-click action in the admin dashboard (`/admin-panel/backups/`).

## Database restore

Restoring replaces the entire database, so it is **deliberately only
available from the server CLI** — never exposed over the web admin panel:

```bash
# Locally:
python manage.py restore_db mymoney_20260101_120000.sql

# In Docker:
docker compose exec web python manage.py restore_db mymoney_20260101_120000.sql
```

## Production deployment

1. Provision an Ubuntu VPS with Docker and Docker Compose installed.
2. Point your domain's DNS A record at the VPS.
3. Copy this repository to the VPS, then `cp .env.example .env` and fill in:
   - `DJANGO_SECRET_KEY` — a long random string (e.g. `python -c "import secrets; print(secrets.token_urlsafe(50))"`)
   - `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` — your real domain(s)
   - `POSTGRES_PASSWORD` — a strong password
   - `DOMAIN` — your real domain, so Caddy can obtain a Let's Encrypt certificate automatically
4. `docker compose up -d --build`
5. `docker compose exec web python manage.py createsuperuser`
6. Set up a cron job (or systemd timer) on the host to run
   `docker compose exec -T web python manage.py backup_db` on a schedule, and
   copy the `backups/` volume off-host regularly.

This repository never deploys itself and never runs destructive commands
against a live server automatically — deployment is always a deliberate,
manual step the project owner runs.

## Demonstration data (development only)

```bash
python manage.py seed_categories   # safe in any environment — just default categories
```

There is deliberately no "seed fake transactions" command: the spec for
this project requires that real user accounts are never populated with
fictional money. If you want demo data for a screen recording, create a
throwaway account through the normal registration flow and enter it by hand.

## Project layout

```
config/            Django settings (base/dev/prod/test), root urls
apps/core/         Shared base models, audit log, currency/money helpers
apps/users/        Auth, registration, profile, settings, account lifecycle
apps/accounts/     Financial accounts, the account ledger, transfers
apps/transactions/ Income/expense transactions, categories, favorites
apps/debts/        Borrowing, lending, repayments, debt dashboard
apps/bills/        Recurring bills and payments
apps/closing/      Daily closing / reopening
apps/budgets/      Monthly category budgets
apps/goals/        Savings goals, forecasting, savings/income calculators
apps/reports/      Monthly report aggregation + CSV/PDF export
apps/dashboard/    The home dashboard
apps/adminpanel/   Administrator dashboard (users, audit log, backups, settings)
api/v1/            Versioned REST API (DRF)
templates/         Server-rendered templates (Tailwind/HTMX/Alpine)
tests/             Automated test suite
docker/            Container entrypoint script
```
