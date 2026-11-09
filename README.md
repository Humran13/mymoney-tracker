# MyMoney Tracker

**Track Your Money. Reach Your Goals.**

A personal financial management web application for Uganda (UGX), built as
a modular Django monolith with a versioned REST API, ready for Android/iOS
clients later. See `DECISIONS.md` for architectural decisions,
`PROJECT_STATUS.md` for current build status, and `CLAUDE.md` for an
engineering-context overview of the codebase.

## Stack

- **Backend:** Python 3.13, Django 5.2 LTS, Django REST Framework
- **Database:** PostgreSQL 16 (never floats for money — all amounts are `DecimalField`/`NUMERIC`)
- **Frontend:** Django templates, HTMX, Alpine.js, a compiled production Tailwind CSS build (no CDN), Chart.js (self-hosted)
- **Infra:** Docker, Docker Compose, Gunicorn, Caddy (standalone mode) or CloudPanel's own reverse proxy (see `docs/CLOUDPANEL.md`), WhiteNoise (static files)
- **Tests:** Django/pytest unit+integration suite, plus real browser end-to-end tests with Playwright (Chromium/Firefox/WebKit) — see `tests/`
- **CI:** GitHub Actions (`.github/workflows/ci.yml`) — lint, dependency audit, frontend build, full test suite against real PostgreSQL, Docker build

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

npm install && npm run build:css     # compiles static/css/tailwind.css (required — see below)
python manage.py runserver
```

Visit http://127.0.0.1:8000/.

**About the frontend build:** Tailwind CSS is compiled from
`static_src/input.css` into `static/css/tailwind.css` (git-ignored, not
committed) using `tailwind.config.js`, which scans `templates/**/*.html`
for classes in use. **Re-run `npm run build:css` after changing any
template's classes** — the compiled file won't contain a class it didn't
see at build time. `npm run watch:css` rebuilds automatically on save
during active template work.

## Docker startup (recommended)

This app supports two deployment modes — see `docs/CLOUDPANEL.md` for the
full comparison and the CloudPanel-specific walkthrough. For a standalone
deployment where this app owns ports 80/443 via its own Caddy container:

```bash
cp .env.example .env                 # fill in DJANGO_SECRET_KEY, POSTGRES_PASSWORD, DOMAIN, etc.
docker compose build
docker compose --profile standalone up -d
docker compose exec web python manage.py createsuperuser
```

`--profile standalone` is what starts the `caddy` service; omit it (see
`docs/CLOUDPANEL.md`) when a reverse proxy you don't control — like
CloudPanel's — already owns ports 80/443 on the box.

The `web` container's entrypoint (`docker/entrypoint.sh`) automatically waits
for PostgreSQL, runs migrations, and seeds default categories on every start.

Visit the domain you set in `DOMAIN` (or `http://localhost` for a local
test run — Caddy will serve it over plain HTTP since it can't obtain a
real certificate for `localhost`).

PostgreSQL data and backup archives are bind-mounted to real host
directories (`POSTGRES_DATA_DIR` / `POSTGRES_BACKUP_DIR` in `.env`,
defaulting to `./data/postgres` and `./backups`) rather than Docker named
volumes, so they survive `docker compose down` (without `-v`), image
rebuilds, and are easy to point a host-level backup job at directly.

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

Unit/integration tests run against SQLite (see `config/settings/test.py`)
so they never require a live PostgreSQL server or touch your dev/production
database:

```bash
pip install -r requirements-dev.txt
pytest tests/ --ignore=tests/e2e
```

### Browser (end-to-end) tests

`tests/e2e/` drives the app in a real browser via Playwright — actual
Chromium/Firefox/WebKit, not a simulated DOM. One-time setup:

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium firefox webkit
```

Run them (each spins up a real Django `live_server` against SQLite, same
as the unit tests, so this is still safe to run anywhere):

```bash
pytest tests/e2e/ --browser chromium   # or firefox / webkit
```

`tests/e2e/test_mobile_viewports_e2e.py` specifically checks for horizontal
overflow and that the mobile nav drawer / quick-add bar work at 320, 360,
390 and 430px — the phone widths this app targets.

### Full suite against real PostgreSQL

This is what CI actually runs (see `.github/workflows/ci.yml`):

```bash
DJANGO_SECRET_KEY=local-ci-test DATABASE_URL=postgres://mymoney:yourpassword@localhost:5432/mymoney_ci \
  pytest tests/ --ds=config.settings.dev --browser chromium
```

## Database backup

```bash
# Locally:
python manage.py backup_db

# In Docker:
docker compose exec web python manage.py backup_db
```

Backups are written to `$POSTGRES_BACKUP_DIR` (default `./backups/`) as
`mymoney_<timestamp>.sql` — a real file on the host, not just inside the
container. This is also available as a one-click action in the admin
dashboard (`/admin-panel/backups/`).

## Database restore

Restoring replaces the entire database, so it is **deliberately only
available from the server CLI** — never exposed over the web admin panel:

```bash
# Locally:
python manage.py restore_db mymoney_20260101_120000.sql

# In Docker:
docker compose exec web python manage.py restore_db mymoney_20260101_120000.sql
```

This restore process was verified end-to-end: a real backup was restored
into a separate, isolated database and its schema and data were confirmed
correct before this guidance was written. See `PROJECT_STATUS.md` and
`docs/CLOUDPANEL.md` for how to rehearse a restore safely before trusting
it against real data on a new VPS.

## Production deployment

**Deploying on a CloudPanel VPS?** See `docs/CLOUDPANEL.md` for the full
walkthrough (directory layout, reverse proxy configuration, backups,
update/rollback procedure). It also lists exactly what has and hasn't been
verified against a real CloudPanel installation.

Standalone deployment (this app owns ports 80/443 via its own Caddy
container):

1. Provision an Ubuntu VPS with Docker and Docker Compose installed.
2. Point your domain's DNS A record at the VPS.
3. Copy this repository to the VPS, then `cp .env.example .env` and fill in:
   - `DJANGO_SECRET_KEY` — a long random string (e.g. `python -c "import secrets; print(secrets.token_urlsafe(50))"`)
   - `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` — your real domain(s)
   - `POSTGRES_PASSWORD` — a strong password
   - `POSTGRES_DATA_DIR` / `POSTGRES_BACKUP_DIR` — real host paths for persistent storage
   - `DOMAIN` — your real domain, so Caddy can obtain a Let's Encrypt certificate automatically
4. `docker compose --profile standalone up -d --build`
5. `docker compose exec web python manage.py createsuperuser`
6. Set up a cron job (or systemd timer) on the host to run
   `docker compose exec -T web python manage.py backup_db` on a schedule
   (see `docs/CLOUDPANEL.md` for a ready-to-use cron line), and copy
   `$POSTGRES_BACKUP_DIR` off-host regularly.

This repository never deploys itself and never runs destructive commands
against a live server automatically — deployment is always a deliberate,
manual step the project owner runs.

### Updating a running deployment

Never `docker compose down -v` against a deployment with real data — that
removes volumes. The safe update sequence (back up → pull → rebuild →
migrate → restart just `web` → verify) is spelled out with exact commands
in `docs/CLOUDPANEL.md`, and applies the same way whether or not you're
using CloudPanel.

## Demonstration data (development only)

```bash
python manage.py seed_categories   # safe in any environment — just default categories
```

There is deliberately no "seed fake transactions" command: the spec for
this project requires that real user accounts are never populated with
fictional money. If you want demo data for a screen recording, create a
throwaway account through the normal registration flow and enter it by hand.

## Email / SMTP

Password reset (and any future notification email) uses Django's email
backend, controlled by `.env`: leave `SMTP_HOST` blank to keep the console
backend (emails are logged, not sent — fine for local testing with
colleagues before you have a provider), or fill in `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS` and
`DEFAULT_FROM_EMAIL` for real delivery. This has been tested against
Django's test email backend (see `tests/test_registration_and_admin.py`
and `tests/e2e/test_auth_e2e.py`'s password-reset tests) — real SMTP
delivery has not been verified against an actual provider, since no
credentials were available in this environment.

## Continuous integration

`.github/workflows/ci.yml` runs on every push/PR: lint (`ruff`), a Python
and npm dependency vulnerability scan (`pip-audit`, `npm audit`), the
Tailwind production build, the full test suite (unit + browser E2E) against
a real PostgreSQL service container, and a Docker image build. It uses only
disposable, test-only credentials and never touches a production database.

## Project layout

```
config/            Django settings (base/dev/prod/test/e2e), root urls
apps/core/         Shared base models, audit log, currency/money helpers
apps/users/        Auth, registration, profile, settings, account lifecycle
apps/accounts/     Financial accounts, the account ledger, transfers
apps/transactions/ Income/expense transactions, categories, favorites
apps/debts/        Borrowing, lending, repayments, debt dashboard
apps/bills/        Recurring bills and payments
apps/closing/      Daily closing / reopening
apps/budgets/      Monthly category budgets
apps/goals/        Savings goals, forecasting, savings/income calculators, "what if" scenarios
apps/reports/      Monthly report aggregation + CSV/PDF export
apps/dashboard/    The home dashboard
apps/adminpanel/   Administrator dashboard (users, audit log, backups, settings)
api/v1/            Versioned REST API (DRF)
templates/         Server-rendered templates (Tailwind/HTMX/Alpine)
static_src/        Tailwind CSS source (compiled to static/css/tailwind.css)
tests/             Unit/integration test suite
tests/e2e/          Playwright browser end-to-end tests
docker/            Container entrypoint script
docs/CLOUDPANEL.md  CloudPanel VPS deployment guide
.github/workflows/ GitHub Actions CI
```
