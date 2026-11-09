# Project Status

**Last updated:** 2026-11-09
**Current milestone:** All 8 milestones built; app is feature-complete for v1 scope. See "Known limitations" before treating this as production-ready.

## Completed features

- **Milestone 1 — Foundation:** Django 5.1 project, custom email-based `User`
  model, PostgreSQL via `django-environ`, Docker/Compose/Caddy/Gunicorn,
  registration, login/logout, password change, **password reset via email**,
  profile, settings, self-deactivation (with auto-reactivation on next
  login), self-service permanent deletion, admin-forced
  deactivation/suspension (enforced at the auth-backend level, not just the
  login form), login rate limiting (django-axes).
- **Milestone 2 — Accounts & money:** `FinancialAccount` + single-ledger
  `AccountPosting` architecture, income/expense tracking with categories,
  mobile money transaction fees (principal + fee posted separately),
  inter-account transfers (not counted as income/expense), first-time
  onboarding wizard for real opening balances.
- **Milestone 3 — Debts:** Borrowing and lending with partial repayments,
  live-computed outstanding balances that carry forward indefinitely
  (there is no month-end reset to carry forward from), overdue detection,
  lending interest recorded as income separately from principal.
- **Milestone 4 — Habits:** Favorites/templates with one-click reuse,
  frequent/recent transaction suggestions, recurring bills with
  due/overdue tracking and a DB-level unique constraint preventing a
  double payment on the same cycle, daily closing (close/reopen, per-day
  breakdown, reconciliation notes), historical corrections (editing/
  deleting anything re-syncs its ledger postings atomically).
- **Milestone 5 — Reporting:** Home dashboard (today's numbers, account
  balances, debts summary, upcoming bills, goal progress, spending trend
  chart, financial-health warnings), monthly report with category
  breakdown, debt carry-forward table, budget performance, CSV/PDF export,
  configurable budget warning thresholds (default 80%/100%).
- **Milestone 6 — Goals:** User-created savings goals only (never
  auto-created), virtual allocations that never double-count real account
  balances, goal forecasting that refuses to guess without ≥14 days of
  history or with non-positive savings capacity, savings target
  calculator, required-income calculator.
- **Milestone 7 — API/PWA/UX:** Versioned REST API (`/api/v1/`) with both
  session and JWT auth, every FK scoped to the requesting user (not just
  list/retrieve — nested FK fields too), installable PWA (manifest, icons,
  a service worker that caches only `/static/` assets and never caches
  pages or API responses), mobile-first responsive UI with light/dark mode.
- **Milestone 8 — Admin & hardening:** Admin dashboard (user search,
  activate/suspend/deactivate/delete with confirmation, audit log, site
  settings incl. registration on/off toggle, system health, one-click
  backup), CSRF/security headers/HSTS/secure cookies in prod, audit
  logging on auth and admin actions, `python manage.py backup_db`, and a
  deliberately **CLI-only** `restore_db` (never exposed over the web, since
  it replaces the whole database).

## Verification performed

- **35 automated tests pass** (`pytest`, runs against SQLite — see
  `tests/`), covering: ledger/fee correctness and Decimal precision,
  borrowing/lending partial repayment + carry-forward, duplicate bill
  payment prevention, budget percentage, goal forecast guardrails,
  cross-user isolation (both web views and the API, including nested FK
  fields), registration, suspension actually blocking login, self-deactivate
  → reactivate-on-login, admin panel access control, password reset
  end-to-end, and a smoke test that every major authenticated page (and
  every admin page) renders without error.
- **The actual Docker stack was built and run live** in this environment
  (Docker Desktop was started, `docker compose build` and `docker compose
  up` were run for real against this project — not simulated). This caught
  and fixed two real bugs that unit tests alone would have missed:
  1. The container `HEALTHCHECK` hit `localhost:8000` directly, which
     `DisallowedHost`-rejected because `ALLOWED_HOSTS` only listed the real
     domain. Fixed by always allowing `localhost`/`127.0.0.1` in `prod.py`
     (safe — the `web` service has no host port mapping).
  2. **An infinite-redirect bug behind the reverse proxy**: Caddy
     terminates TLS and proxies to Gunicorn over plain HTTP, but Django had
     no way to know the original request was HTTPS, so
     `SECURE_SSL_REDIRECT` redirected every request to itself forever.
     Fixed with `SECURE_PROXY_SSL_HEADER`. Verified fixed via `curl` through
     the live Caddy → Gunicorn → Postgres stack, including a full
     login (CSRF token fetch, POST, session cookie) and a JWT token fetch.
  The verification stack (containers, volumes, the test superuser) was
  torn down afterward (`docker compose down -v`) — nothing from it persists.

## Known limitations

- **No "scenario" comparisons on the required-income calculator**
  (reduced-spending / increased-income / extended-deadline variants from
  spec §25) — only the single current-pace calculation is implemented.
- **Tailwind is loaded from its CDN script**, not a compiled production
  build. This is flagged by Tailwind's own docs as unsuitable for
  high-traffic production (it compiles in-browser). Swapping to a compiled
  build later only touches one `<script>` tag — see `DECISIONS.md`.
- **No automated browser/E2E tests** (Selenium/Playwright). The test suite
  is Django-test-client integration tests (real views, templates, DB) plus
  the one manual live-stack verification described above — not full
  browser automation.
- **No CI/CD pipeline file** (e.g. GitHub Actions) was created — running
  tests and deploying are both manual steps per `README.md`.
- **Transactional email is not configured with a real provider.** Password
  reset and any future notification email work (the Django flow, console
  backend by default) but an operator needs to set `DJANGO_EMAIL_BACKEND`
  and SMTP credentials in `.env` for real delivery in production.
- **No automated test exercises axes' rate-limiting lockout itself**
  (it's configured and enabled in prod, disabled in dev/test for
  developer convenience) — this is configuration, not custom code, so the
  risk is lower, but it hasn't been exercised end-to-end here.
- Load/performance testing was not performed.

## Next action (if resuming this project)

Nothing is blocking — the app satisfies the completion requirements listed
in the original spec (§38). If continuing, the highest-value next steps in
priority order would be: (1) configure real SMTP for password reset emails
before any real users register, (2) add the required-income scenario
comparisons, (3) replace the Tailwind CDN script with a compiled build
before high-traffic production use, (4) add a CI workflow that runs
`pytest` on every push.

## External blockers

None. Docker, Python, and PostgreSQL were all available in this
environment; everything in this status file was actually run, not assumed.
