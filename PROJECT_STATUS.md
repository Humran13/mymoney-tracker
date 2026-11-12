# Project Status

**Last updated:** 2026-11-12
**Current phase:** Phase 13 (financial accuracy &amp; dashboard improvements) —
code complete and tested; **not yet deployed** (this session had no VPS access).
See `PHASE13_HANDOFF.md` for the full record.

## Phase 13 — financial accuracy (2026-11-12)

**Starting-balance boundary fixed in the calculation itself.** An account's
starting date is its declared `opening_balance_date`, or — when none was ever
declared — the day the account was added. Entries dated before it are already
inside the starting balance, so recording an old debt can no longer inflate
today's cash. This needs no data migration and no user action, and existing
records are untouched. See `PHASE13_HANDOFF.md` §0.

- New **Income vs Expenses Analysis** page (`/reports/income-vs-expenses/`):
  defaults to today, 7/30/60-day and custom ranges, per-day breakdown, weekly
  comparison, per-day drill-down. Cash movement and accrual income/expenses are
  shown as two clearly separated statements.
- Dashboard now shows a **monthly Income | Expenses | Difference** card with a
  month picker, and the "expenses exceed income" warning is clickable, names the
  shortfall amount and links to the analysis page.
- **Charity** and **Shipping Fees** expense categories added (idempotent data
  migration; global, so every existing user gets them with no per-user rewrite).
- **Optional transaction fee on debt repayments** (`BorrowingRepayment.fee_amount`,
  default 0): reduces the paying account and counts once as an expense, never
  reduces the debt principal. Repayments can now also be edited and deleted.
- **"Last 24 Hours"** added to the spending-trend selector. Honest limitation:
  transactions store a date, not a timestamp, so this is today's calendar-day
  total, labelled as such in the UI.
- **Historical-debt double-counting**: the real cause was that every
  pre-existing account has `opening_balance_date = NULL`, so the Phase 11 cutoff
  never applied — plus onboarding never defaulted the date. New accounts now
  default it; existing accounts are surfaced on a new read-only
  **`/accounts/reconciliation/`** screen. **No real balance was changed**:
  applying a snapshot date is a two-step, audited, reversible action the owner
  must take explicitly.
- Internal transfers no longer inflate the dashboard's / Daily Closing's
  "received" and "spent" figures (only the transfer fee counts).
- **Tests:** 272 unit/integration (was 136 at the start of Phase 13) and 347
  across the whole tree in CI order (Chromium); the Phase 13 E2E suite also
  passes on Firefox and WebKit. `ruff` clean, `manage.py check` clean.
- `ruff` clean, `manage.py check` clean.
- **GitHub:** pushed to `origin/master` (head `4bbeb41`); CI run `43233071723`
  green on all 5 jobs, with 318 tests passing against real PostgreSQL.
- **Deployment: pending.** See `PHASE13_HANDOFF.md` §8 for the exact, previously
  used backup → migrate → verify sequence.

## Phase 10 — live deployment (2026-11-10)

Deployed to the owner's existing production CloudPanel VPS (shared with
other live sites — nothing else on that server was disrupted; verified
before and after).

- **Domain:** https://money.therealworldboosts.com (DNS + Let's Encrypt
  cert both live and verified)
- **Deploy path:** `/home/therealworldboosts-money/mymoney-tracker/`
  (`app/`, `data/postgres/`, `backups/postgres/`), site user
  `therealworldboosts-money`
- **Commit deployed:** `3fcafc3` (includes a fix made *during* this
  deployment — see below)
- **CloudPanel site:** created via `clpctl site:add:reverse-proxy`,
  pointed at `127.0.0.1:8001`; cert issued via `clpctl
  lets-encrypt:install:certificate`. CloudPanel's own Nginx owns ports
  80/443; this app's standalone Caddy profile was never started.
- **Compose project name:** `mymoney-tracker` (set via
  `COMPOSE_PROJECT_NAME` in `.env`) — no collision with the VPS's other
  Docker projects (`daily-figures`, `spidqah-collections-app`).
- **A real bug was found and fixed during this deployment**: deleting a
  user who had ever recorded a transaction, transfer, borrowing, or
  lending raised `ProtectedError` and would have crashed for essentially
  every real user. Root cause, fix, and regression tests in
  `apps/core/user_deletion.py` / `tests/test_user_deletion.py` (commit
  `3fcafc3`). CI passed, then redeployed and re-verified live.
- **Live smoke test**: 15/15 automated checks passed against the real
  public domain (login, mobile nav + no horizontal overflow at 390px,
  income/expense, mobile-money fee, transfer, borrowing+repayment,
  lending+repayment, daily closing close/reopen, monthly report, savings
  goal + "what if" scenario, admin-panel isolation for a non-staff user) —
  using a disposable test account created and fully deleted afterward.
- **Backup/restore verified live**: a real backup was taken, restored
  into an isolated database on the same production Postgres instance, and
  confirmed to contain the correct schema (33 tables) and the correct
  admin record before being dropped.
- **Daily backups automated** via `/etc/cron.d/mymoney-tracker-backup`
  (02:15 server time, 30-day retention), permissions tightened to `600`
  owned by the site user.
- **Administrator account**: `humranaby@gmail.com`, superuser + staff
  confirmed, but **created with no password set** — several attempts to
  generate/relay a password through this session were correctly blocked
  by the platform's own credential-handling safeguards, so the owner sets
  their own password directly over SSH (`docker compose exec web python
  manage.py changepassword humranaby@gmail.com`), which never passes
  through this session at all. This is pending the owner's action.
- **Public registration**: confirmed disabled on the live site (the
  actual toggle is a DB-backed `SiteConfiguration` row, not just the
  `.env` value — the env var alone had no effect until this was set
  directly; worth knowing if you ever wonder why toggling `.env` didn't
  seem to do anything).
- **SMTP**: not configured (no credentials provided) — password reset
  will log to console/nowhere until `SMTP_HOST` etc. are set in `.env` on
  the server and the `web` service is restarted.
- **CloudPanel remote backups**: this CloudPanel installation (CLI 6.0.8)
  has no backup/remote-backup feature at all — confirmed by dumping its
  full command list. Google Drive backup support is a CloudPanel Pro
  feature. See `docs/CLOUDPANEL.md` for what this means and the
  documented alternative (`rclone`, installed but unconfigured).
- **Existing VPS services** (`daily-figures`, `spidqah-collections-app`,
  `hbbr`/`hbbs`) confirmed still running/healthy with unchanged uptimes
  after this deployment.
- **Two documentation bugs in `docs/CLOUDPANEL.md` itself were found and
  fixed while actually following it**: the Postgres UID was wrong (`999`
  assumed vs. `70` actual, for this Alpine-based image — verify, don't
  assume, is exactly the lesson), and the suggested
  `docker compose run --rm web python manage.py migrate` step for
  previewing migrations doesn't work as written, because the image's
  `ENTRYPOINT` ignores the command passed to `docker compose run` and
  always runs its own full startup sequence regardless.

## Phase 9 — what was done

- **Goal scenarios finished.** `apps/goals/services.goal_scenario()` adds
  Scenarios B/C/D/E (reduce spending, increase income, change deadline,
  combine all three) on top of the existing baseline forecast — all purely
  hypothetical, never writing to a real transaction, account, or goal.
  `goals:required_income` also gained a deadline-extension scenario across
  all active goals. 8 new tests in `tests/test_goal_scenarios.py`.
- **Mobile experience fixed, not just reviewed.** The desktop sidebar was
  `hidden md:flex` with **no replacement navigation on mobile at all** —
  a real, severe bug, not a polish item. Added a slide-over mobile nav
  drawer and an always-reachable "+ Income / + Expense" quick-add bar
  fixed to the bottom of the screen on phone widths. Verified at 320, 360,
  390, 430px in real Chromium, Firefox, and WebKit via Playwright.
- **Tailwind compiled, not CDN.** `npm run build:css` compiles
  `static_src/input.css` → `static/css/tailwind.css` via `tailwind.config.js`
  scanning the templates; Dockerfile has a dedicated Node build stage.
- **Real browser E2E tests** (`tests/e2e/`, Playwright): registration,
  login, logout, password reset, suspension, financial accounts, income/
  expense/transfers/mobile-money fees, borrowing/lending, recurring bills,
  daily closing, budgets, goals + scenarios, reports (incl. CSV download),
  admin user management, and the mobile-viewport suite described above —
  39 tests, run and passing on **all three** engines (Chromium, Firefox,
  WebKit), not just claimed.
- **Three real bugs found and fixed via this testing** (not hypothetical —
  each one failed a real browser test first):
  1. `templates/transactions/transaction_form.html` crashed
     (`VariableDoesNotExist`) when a recent transaction had no category —
     masked in production by Django's lenient default, surfaced by
     Django's stricter test-mode template rendering.
  2. The dashboard overflowed horizontally at 320px: a Chart.js canvas
     broke out of its CSS Grid cell (`min-width: auto` default). Fixed
     with `min-w-0` + a hard `max-width: 100% !important` on the canvas.
  3. Chart.js loaded from cdnjs was blocked by WebKit's strict MIME
     checking — fixed by self-hosting `chart.umd.min.js`.
- **Financial engine hardened**: `BorrowingForm`/`LendingForm` were
  missing a check that the amount is positive (fixed); repayments can no
  longer exceed the outstanding balance, enforced at **both** the form
  layer (web) and the service layer (so the API can't bypass it) — see
  `apps/debts/services.py` and `apps/debts/forms.py`. 12 new edge-case
  tests (`tests/test_financial_edge_cases.py`): zero/negative amounts,
  overpayment, a real race-condition test proving the DB unique constraint
  (not just the application-level check) blocks a duplicate bill payment,
  month-boundary budget scoping, and backfilling a transaction onto an
  already-closed day.
- **Security**: `pip-audit` found ~40 known CVEs against the originally
  pinned Django 5.1.4/DRF 3.15.2/SimpleJWT 5.3.1 — upgraded to Django
  5.2.19 (LTS), DRF 3.18.3, SimpleJWT 5.5.1; `pip-audit` now reports zero
  known vulnerabilities, `npm audit` zero as well. Added tests proving
  CSRF actually rejects an unsigned POST, login lockout (django-axes)
  actually locks out after repeated failures, and a garbage JWT is
  actually rejected — not just configured, verified. Production settings
  now raise `ImproperlyConfigured` at startup if `DJANGO_SECRET_KEY` is
  still the insecure dev default.
- **SMTP configured** (`SMTP_HOST`/`PORT`/`USERNAME`/`PASSWORD`/`USE_TLS`,
  `DEFAULT_FROM_EMAIL` in `.env`) with a console-backend fallback when
  unset. Tested against Django's test email backend; **real delivery
  against an actual SMTP provider was not verified** — no credentials
  were available in this environment.
- **GitHub Actions CI** (`.github/workflows/ci.yml`): lint (ruff),
  dependency audit (pip-audit + npm audit), frontend build verification,
  the full test suite (unit + all-browser-capable E2E, run with Chromium
  in CI for speed) against a **real PostgreSQL service container**, and a
  Docker image build. This exact pipeline was run locally first — a
  disposable Postgres container, the same migrate+test commands CI uses —
  before being trusted in the workflow file.
- **CloudPanel deployment prepared** (`docs/CLOUDPANEL.md`): `compose.yaml`
  now supports two modes via a `standalone` Compose profile (own Caddy +
  ports 80/443) vs. CloudPanel-managed (no public port of its own; reaches
  the app via a loopback-only `127.0.0.1:$APP_PORT`, routed to by
  CloudPanel's own reverse proxy). PostgreSQL data and backups moved from
  named Docker volumes to configurable bind mounts
  (`POSTGRES_DATA_DIR`/`POSTGRES_BACKUP_DIR`) so they land under a real,
  inspectable host path — one a CloudPanel site's own backup routine could
  actually reach, unlike a named volume under `/var/lib/docker/volumes/`.
- **Backup and restore verified for real, not just scripted.** A backup
  was taken from a live container, restored into a separate isolated
  database in the same Postgres instance, and the restored schema (33
  tables) and a specific test record (exact email, exact balance) were
  confirmed correct by direct query. See `docs/CLOUDPANEL.md` for the
  reproducible steps.
- **Secret audit before publication**: `gitleaks` run against the full
  git history (0 leaks) and the working tree's actual source (the
  only hits were inside third-party `.venv` package files from an overly
  broad glob, never committed); a targeted grep for AWS/GitHub/Stripe/
  Slack key patterns and for any ever-committed `.env`/key/credential file
  across all history — all clean. `.gitignore`/`.dockerignore` tightened
  (`.env.*` with `!.env.example`, bind-mount data dirs, Playwright
  artifacts).

## Verification performed (Phase 9)

- **102 automated tests pass**: 63 unit/integration (SQLite) + 39 browser
  E2E. The full 102 were also re-run against a **real, disposable
  PostgreSQL container** (not SQLite) to validate the exact CI pipeline
  before trusting the workflow file — also passed, in ~4.5 minutes.
- **39 E2E tests × 3 browser engines** (Chromium, Firefox, WebKit) — all
  green. Finding and fixing the WebKit-specific issues (networkidle timing,
  the Chart.js CDN MIME bug) is what makes this a genuine cross-browser
  claim rather than a Chromium-only one with "should work elsewhere" hope.
- **Dependency audit clean**: `pip-audit` (0 known vulnerabilities after
  the Django/DRF/SimpleJWT upgrade) and `npm audit` (0).
- **Docker build verified** with the upgraded dependencies.
- **CloudPanel-mode stack (db + web, no Caddy) run live**: health check
  reachable via `127.0.0.1:8001`, Postgres data confirmed as real files
  under a bind-mounted host directory, backup produced a real host file,
  restore into an isolated database confirmed correct.
- **Secret scan clean** (see above) before any publish step was attempted.

## Known limitations

- **Real SMTP delivery not verified** — configured and unit-tested against
  Django's test backend only; no live provider credentials were available.
- **CloudPanel reverse-proxy configuration not verified against a real
  CloudPanel installation** — this environment had no CloudPanel instance
  to configure. `docs/CLOUDPANEL.md` marks every such step explicitly.
- **CloudPanel's own backup-scope coverage of the bind-mounted backup
  directory is unconfirmed** — depends on the specific CloudPanel version
  on the real VPS; documented as a step to check on first deploy.
- **No Google Drive (or other) remote backup destination is configured** —
  none was requested with real credentials, and none should be invented.
- Tailwind's production build and Chart.js self-hosting resolve the
  previously-flagged frontend CDN concerns from the v1 status report.
- Deployment to the live VPS itself was explicitly **not** authorized for
  this phase and was not attempted.

## Next action (if resuming this project)

Nothing is blocking further local development. If/when VPS access becomes
available: follow `docs/CLOUDPANEL.md` section by section, checking off
each "[VPS-ONLY]" item as it's confirmed, starting with directory
permissions and the reverse-proxy configuration.

## External blockers

- No CloudPanel installation or live VPS was available to this session —
  everything in `docs/CLOUDPANEL.md` marked **[VPS-ONLY]** needs that
  access to actually confirm, as opposed to the backup/restore/Postgres-
  persistence mechanics underneath it, which were verified directly.
- No real SMTP provider credentials were available or should be invented.
- No Google Drive / remote backup credentials were available or should be
  invented.

## GitHub publication

- **Repository:** https://github.com/Humran13/mymoney-tracker
- **Visibility:** Public
- **Branch:** `master`
- **Commit:** `c3ba02a6733fb8781a5b5d38ff6ff563b7d2f6bb` ("Phase 9: finish goal
  scenarios, fix mobile UX, harden security, add CI/E2E, prep CloudPanel
  deploy")
- **CI status:** All 5 jobs passed on GitHub's own infrastructure
  (run `42674587796`): `dependency-audit`, `lint`, `test` (3m13s — full
  suite against a real PostgreSQL service container), `docker-build`,
  `frontend-build`.
- **Secret scan:** `gitleaks` against the full 2-commit history — 0 leaks —
  run immediately before this push.
