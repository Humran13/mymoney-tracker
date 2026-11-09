# Architectural Decisions

Decisions made independently while building MyMoney Tracker, per the
"choose a secure and practical default, document it, continue" instruction.
Newest at top isn't required here — this is organized by topic instead.

## Money & ledger

- **Single ledger (`AccountPosting`), not a mutated balance field.** Every
  module that moves money (transactions, transfers, borrowing/lending,
  bill payments) writes signed rows to one `AccountPosting` table instead
  of incrementing `FinancialAccount.balance` directly. An account's balance
  is always `opening_balance + sum(postings)`, computed on read. This is
  what makes "prevent duplicate postings" and "recalculate balances after
  historical corrections" trivial and provably correct, instead of relying
  on careful bookkeeping at every call site.
- **`sync_postings()` is the only writer.** Editing or deleting a
  transaction/transfer/repayment always deletes that source object's
  existing postings and recreates them in one atomic transaction
  (`apps/accounts/services.py`). There is no path that appends postings
  without first clearing old ones for the same source, so edits can never
  leave duplicates behind.
- **All money is `Decimal`/`NUMERIC(18,2)`, never float.** `apps/core/models.py`'s
  `money_field()` is the single place that defines this; Python `Decimal`
  arithmetic and Postgres `NUMERIC` are both exact.
- **UGX is formatted with 0 decimal places** (nobody writes "shs 5,000.00"),
  but amounts are stored with 2 decimal places so a future currency that
  does use cents doesn't require a schema change. See `apps/core/currency.py`
  — it's a small registry, not scattered `if currency == "UGX"` checks.

## Savings goals

- **Goal allocations are virtual, not real transfers.** Adding money to a
  goal does not move it between accounts or create a posting — it's an
  earmark against money the user already has. This is what the spec means
  by "goal allocations must correspond to real funds... avoid double-counting
  money": a shilling is never simultaneously free cash and locked savings in
  two different ledgers. The goal list page shows a warning if a user has
  allocated more across active goals than they currently hold.
- **No goal is ever created automatically.** `SavingsGoal` rows only come
  from an explicit user action.
- **Forecasts refuse to guess.** If there's under 14 days of transaction
  history, or the user's daily income minus expenses is zero or negative,
  `forecast_goal()` returns `estimated_completion_date = None` with an
  explanation instead of inventing a date.

## Debts

- **Outstanding balances are always computed live**, never reset or
  snapshotted at month-end — so "debts carry forward" is just a property
  of not having a reset step, not a feature that had to be built.
- **Lending interest received is posted as `source_type=INCOME`** (distinct
  from the principal repayment posting), so it naturally appears in income
  totals without a separate code path, while principal repayment is not
  counted as income.

## Recurring bills

- **`BillPayment` has a unique constraint on `(bill, due_date)`.** This is
  what actually prevents a double-payment on the same cycle, rather than
  relying on UI state (which a double-click or two open tabs can defeat).

## Daily closing

- **"Closing a day" is a workflow marker, not a lock.** Because balances
  are always computed from the full posting history rather than a mutable
  running total, there's no technical reason to block edits to a closed
  day — reopening it and backfilling a forgotten transaction just changes
  what `balance_as_of()` returns for every date on or after it, automatically.

## Auth & accounts

- **Email is the username field.** `apps/users/models.py` replaces Django's
  default `username` with a unique `email`.
- **Suspension is enforced in a custom `AuthenticationBackend`**
  (`apps/users/backends.py`), not just in the login form. The form's
  `confirm_login_allowed()` gives a friendlier message, but the backend is
  what actually makes `authenticate()` reject a suspended user regardless
  of which code path calls it — tests proved the form-only version could be
  bypassed by any direct `authenticate()`/`login()` call.
- **Two separate deactivation flags.** `is_active` (Django's own flag) is
  reserved for administrator-forced deactivation and blocks login outright.
  `is_deactivated` is for the user's own "deactivate my account" action —
  it doesn't block login; logging in again automatically clears it ("log in
  to reactivate," per the spec) while leaving the admin-level flag untouched.
- **Public registration is a DB-backed toggle** (`SiteConfiguration`
  singleton in `apps/adminpanel`), not just an env var, so an administrator
  can close registration without a redeploy.

## Admin panel

- **Database restore is CLI-only**, never a web button. Backup is a safe
  one-click action (it only reads); restore replaces the entire database,
  so it's deliberately kept out of the web attack surface entirely, per the
  "no unrestricted destructive operation through the web admin" requirement.
- **Financial "corrections" happen through the same validated forms/services
  as normal usage** — there is no raw balance-editing field anywhere, admin
  or otherwise. An administrator who needs to fix a user's data does so by
  using Django's admin on the underlying rows (transactions, postings, etc.),
  which still goes through the same model validation.

## API

- **DRF serializers scope every foreign key to the request's own user**
  (`ScopedFieldsMixin` in `api/v1/serializers.py`). Without this, DRF's
  default `PrimaryKeyRelatedField` validates against the *entire* table, so
  a user could reference another user's account/borrowing/goal id directly
  in a POST body even though list/retrieve endpoints are correctly scoped.
  This was caught and fixed by `tests/test_isolation.py`.
- **JWT for future mobile clients, session auth for the web app** — both
  are enabled simultaneously (`DEFAULT_AUTHENTICATION_CLASSES`), so the
  Android/iOS apps planned for later phases don't require any backend change.

## Frontend

- **Tailwind compiled via the Tailwind CLI, not the CDN script.** (Updated
  in Phase 9 — v1 originally used `cdn.tailwindcss.com` for build
  simplicity; Tailwind's own docs flag that as unsuitable for production
  since it compiles in-browser.) `static_src/input.css` is compiled to
  `static/css/tailwind.css` by `npm run build:css`, scanning
  `templates/**/*.html` per `tailwind.config.js`. The Dockerfile has a
  `node:20-slim` build stage that runs this and copies just the compiled
  CSS into the final image — Node itself never ships in the runtime image.
  **Consequence to remember:** the compiled file only contains classes
  Tailwind saw at build time, so changing a template's classes requires
  re-running the build — this bit us once during Phase 9 (added
  `overflow-hidden`/`min-w-0` to fix a layout bug, class had no effect
  until rebuilt) before it was caught by the mobile-viewport E2E test.
- **Chart.js is self-hosted** (`static/js/chart.umd.min.js`), not loaded
  from a CDN. Found via real WebKit testing: cdnjs served it with a
  Content-Type that WebKit's strict MIME-sniffing (`X-Content-Type-Options:
  nosniff`) refused to execute, silently breaking the dashboard chart in
  Safari/WebKit specifically. Self-hosting removes both the cross-browser
  risk and a third-party runtime dependency.
- **No JS framework/bundler.** HTMX + Alpine.js cover the interactivity this
  app needs (toggles, confirmations, live theme switching) without a
  separate frontend build pipeline, matching "avoid architectural
  overengineering."

## Infra

- **No Celery/Redis.** Bill reminders and overdue-debt warnings are computed
  synchronously from live queries at page-render time, not via a background
  job queue — there's no scheduled task in this app that couldn't just be
  "check when the page loads." This removes a whole extra container and
  failure mode for a v1 feature set that doesn't need it.
- **Backups via `pg_dump`/`psql` subprocess calls**, not a Python-level DB
  dump library — this is what production Postgres backup tooling actually
  uses, and it means a restored dump is a plain SQL file an operator can
  also inspect or run by hand if the app's own restore command isn't available.

## Phase 9 additions

- **Django/DRF/SimpleJWT upgraded** (5.1.4→5.2.19, 3.15.2→3.18.3,
  5.3.1→5.5.1) after `pip-audit` found ~40 known CVEs against the
  originally-pinned versions. Landed on Django's 5.2 **LTS** line rather
  than jumping to the newer 6.x major that was also available — safer for
  an app this size to stay on a long-term-supported release than to absorb
  an untested major-version bump at the same time as a security patch.
  Full test suite (unit + E2E, all three browsers) re-verified green after
  the upgrade before this was considered done.
- **Goal "what-if" scenarios reuse the same calculation core as the
  baseline forecast** (`_required_savings_for_deadline()` in
  `apps/goals/services.py`), parameterized by hypothetical
  income/expense/deadline adjustments instead of duplicating the math.
  Scenario A (current pace) is literally the same function called with
  every adjustment left at zero — there's one savings-math implementation,
  not two that could drift out of sync.
- **Debt repayment overpayment is blocked at both the form layer (for a
  good inline web error) and the service layer** (`record_borrowing_repayment`/
  `record_lending_repayment` raise `ValueError`) — the form alone would
  have left the REST API able to record a repayment larger than the
  outstanding balance, since DRF serializers never go through Django forms.
- **Real browser testing (Playwright) caught three bugs unit tests
  structurally couldn't**: a template crash when a transaction has no
  category (`VariableDoesNotExist` under Django's test-mode strict
  variable resolution — masked by production's lenient default), a
  genuine mobile layout overflow at 320px from a Chart.js canvas breaking
  a CSS Grid item's `min-width: auto` default, and the WebKit CDN
  MIME-type issue described above. None of these were reachable by a
  Django-test-client-only suite, which never renders CSS or executes JS.
- **`DJANGO_ALLOW_ASYNC_UNSAFE=1` is set only inside `tests/e2e/conftest.py`**,
  never in application settings. pytest-playwright's sync wrapper leaves an
  event loop active in the test thread, which trips Django's sync-DB-in-
  async-context guard for any ORM call made directly from a test body
  (not through a real request). This is a test-harness quirk, not a
  statement about the app's own concurrency model — the running
  application is never started with this flag.
- **CI runs the full suite against a real PostgreSQL service container**,
  not SQLite, using `pytest --ds=config.settings.dev` to override
  `pytest.ini`'s SQLite default for local runs. `--ds` was required
  specifically because pytest-django's ini-file setting otherwise takes
  precedence over a same-named environment variable — verified locally
  against a disposable Postgres container before trusting it in CI.
- **PostgreSQL data and backups moved from named Docker volumes to
  configurable bind mounts** (`POSTGRES_DATA_DIR`, `POSTGRES_BACKUP_DIR`).
  A named volume lives under `/var/lib/docker/volumes/`, outside any
  CloudPanel site's home directory, so a host-level or CloudPanel backup
  job would silently never cover it. Verified end-to-end locally: real
  Postgres data files appear in the bind-mounted host path, a `backup_db`
  run produces a real file there too, and that file was successfully
  restored into a separate isolated database with matching schema and data.
- **Caddy is now opt-in via a Compose profile** (`--profile standalone`)
  instead of always starting. A CloudPanel deployment needs this app to
  *not* claim ports 80/443, since CloudPanel's own reverse proxy already
  owns them for the whole VPS; the profile is what makes both deployment
  modes work from the same `compose.yaml` without a second compose file.
- **Production settings validate `DJANGO_SECRET_KEY` at import time**,
  raising `ImproperlyConfigured` if it's still the insecure dev default —
  the one setting where failing loudly at startup is much better than
  silently running production on a known, guessable key.
