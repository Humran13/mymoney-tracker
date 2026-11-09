# Project Status

**Last updated:** 2026-11-09
**Current phase:** Phase 9 (final hardening, mobile optimization, testing,
GitHub publication, CloudPanel deployment prep) — complete except for the
items under "External blockers" below, which require live VPS/CloudPanel
access this environment doesn't have.

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

See the final report in this session's conversation for the actual
repository URL, visibility, branch, commit hash, and CI status at time of
publication — this file is updated at the next meaningful checkpoint after
that to include it here too, per the "don't restate things repeatedly"
token-efficiency rule for this phase.
