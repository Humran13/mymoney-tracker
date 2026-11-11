# Phase 11 Handoff

**Last updated:** 2026-11-11 — a later session deployed this phase to
production. Sections below from 2026-11-10 are left as-is (historical
record of the dev work); this note plus the inline updates mark what
changed once real VPS/DB access was available.
**For:** whichever assistant (Claude Code, DeepSeek, or a human) picks this up next.
Read this before touching the code — it tells you exactly what's done, what
isn't, and the one thing you must not skip (production DB access is blocked
for the account this session had).

## 0. 2026-11-11 deployment update (read this first)

- **Fixed before deploying:** `apps/feedback/migrations/0001_initial.py` had
  baked in the dev machine's absolute Windows path
  (`D:\mymoney-tracker\private_media\feedback_screenshots`) via
  `FileSystemStorage`'s default `deconstruct()`. Fixed with a custom
  `PrivateScreenshotStorage.deconstruct()` in `apps/feedback/models.py` that
  returns no location kwarg, regenerated the migration (same filename, never
  applied anywhere before this). Commit `e266e85`.
- **Found by CI, fixed before deploying:** the feedback form overflowed
  horizontally at 360px — `input[type="file"]` had no width rule in
  `static_src/input.css`. Fixed, rebuilt `static/css/tailwind.css`. Commit
  `904f0d5`.
- **Pushed to GitHub.** `origin/master` head is now `904f0d5`. CI green on
  all 5 jobs (verified via `gh run view --json status,conclusion,jobs`, not
  the watch command's own exit code — see the project's CI lesson about
  that).
- **Deployed to production** (https://money.therealworldboosts.com).
  Backed up the production database first and verified the backup by
  restoring it into an isolated throwaway database and checking exact row
  counts and the real account balances matched before touching anything.
  Pulled `904f0d5` as the site user (fast-forward only), added
  `PRIVATE_MEDIA_DIR` to the server's `.env` (34 existing lines untouched),
  rebuilt and restarted only the `web` service. `db` container was never
  restarted; all other unrelated VPS services (`daily_figures_app`,
  `spidqah-collections-app-*`, `hbbr`, `hbbs`) confirmed healthy before and
  after.
- **Migrations applied for real**, confirmed via container logs:
  `accounts.0003_financialaccount_opening_balance_date_and_more` and
  `feedback.0001_initial`. Re-checked immediately after: every real
  account's `opening_balance` is byte-for-byte unchanged, and the new
  `opening_balance_date` column is `NULL` on all of them (expected — the
  migration is additive-only, exactly as section 7 below predicted).
- **Verified live, with a disposable test account (created and then fully
  deleted via `delete_user_and_all_data()` afterward — no real user data
  touched)**: Cash Safe account type selectable, `opening_balance_date`
  field present on the account form, back links present, feedback
  submission + screenshot upload works, and — the one with real security
  weight — a **second** disposable account and an **anonymous** request
  were both correctly denied access to the first account's screenshot
  attachment (redirected to login / blocked), while the owning account
  could fetch it fine.
- **Registration stays disabled, correctly.** Checked the live container's
  actual environment: `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_HOST_USER`, and
  `EMAIL_HOST_PASSWORD` are all unset in production. SMTP is confirmed not
  configured — this isn't inherited from the 2026-11-10 note below, it was
  independently re-checked this session. Public registration must stay off
  until that changes; nothing was flipped.
- **Still pending the owner's explicit approval, untouched by this
  session**: the real "Wallet & Safe" account's `opening_balance_date` /
  historical split (section 13, items 1–2) — this session deployed the
  *capability* to set them, not the actual values, per the owner's own
  instruction not to touch that row without sign-off.

## 1. Project description and architecture

MyMoney Tracker: a Django 5.2 monolith personal-finance tracker for Uganda
(UGX). See `CLAUDE.md` in the repo root for the architecture primer (ledger
model, isolation enforcement, test layout) — read that first, it's short
and this handoff doesn't repeat it. See `DECISIONS.md` for *why* some things
are built the way they are. See `PROJECT_STATUS.md` for the full history
through Phase 10 (this is Phase 11).

## 2. GitHub repository and branch

- https://github.com/Humran13/mymoney-tracker
- Branch: `master`
- **Commits made this phase are local only, NOT pushed yet** (no instruction
  to push was given; pushing is a visible, shared-state action). Head of
  `master` locally: `859c9fd` ("Phase 11 Priority 5: Help & Feedback").
  Commits this phase, oldest first:
  - `6eea9c2` Priority 1: opening-balance cutoff fix
  - `4e0eda5` Priority 2 (partial): duplicate-submission guard
  - `1f3c585` Priority 3: Back buttons
  - `859c9fd` Priority 5: Help & Feedback
  - (Priority 4 required no new commit — see section 5)
- Before pushing: run `pytest tests/ --ignore=tests/e2e -q` and the E2E
  suite one more time (see section 9) against whatever state the branch is
  in, then push and let CI (`.github/workflows/ci.yml`) confirm independently.

## 3. Production deployment details

- Live at https://money.therealworldboosts.com
- Deploy path on the VPS: `/home/therealworldboosts-money/mymoney-tracker/`
- Site user: `therealworldboosts-money`
- **This session's SSH account (`humran@38.242.134.54`) has NO access to
  that directory or user** — confirmed with `ls`, got `Permission denied`.
  Nothing in this phase was deployed, and no production database was
  touched, read, or connected to. Every finding and fix below is from the
  local clone only, verified with local SQLite (unit tests) and a local
  Playwright browser (E2E tests) — never against production data.
- **To deploy this phase's commits**, whoever has real access to that site
  user (or sudo) needs to: pull/fetch these commits onto the server, run
  `docker compose run --rm web python manage.py migrate` (two new
  migrations: `apps/accounts/migrations/0003_...` and
  `apps/feedback/migrations/0001_initial.py` — both additive, see section 11
  for exactly what they do and why neither touches existing data), add
  `PRIVATE_MEDIA_DIR` to the server's `.env` (see `.env.example`), rebuild/
  restart the `web` service, then re-run the live smoke checks the same way
  Phase 10 did.

## 4. All five numbered requirements (from the original brief)

1. Fix missing Back buttons.
2. Separate Cash Wallet and Cash Safe accounts.
3. **Critical**: fix opening balance / historical transaction double-counting.
4. Enable secure user registration.
5. Create a Help & Feedback section.

(Numbered as given; this session worked them in the brief's stated priority
order — correctness bug first — not the numbering order above.)

## 5. Completed tasks

### Priority 1 — opening balance cutoff bug (commit `6eea9c2`) — DONE, not yet applied to the real account
Root cause confirmed: `FinancialAccount` had no concept of "the date this
opening balance was actually true" — `account_balance()` summed *every*
posting ever recorded, so a historical loan dated before the snapshot date
got deducted a second time. Added `opening_balance_date` (nullable;
`None` = old include-everything behavior, so the migration changes zero
existing balances). `account_balance()`/`bulk_account_balances()` now only
sum postings with `occurred_on >= opening_balance_date` when it's set.
12 new tests in `tests/test_opening_balance_cutoff.py`, all passing.

**Not done, needs the owner's decision (see `PENDING — needs approval`
below):** the real "Wallet & Safe" account's `opening_balance_date` is
still unset in whatever database is live. Someone with DB access needs to
decide the real historical facts (per the brief: UGX 631,200 true opening
balance on 2026-11-09, two loans before that date totaling 850,000, current
value manually inflated to 1,481,200 to compensate) and then, with the
owner's explicit sign-off: set `opening_balance_date=2026-11-09` and
`opening_balance=631200.00` on that one account row. This session did not
touch that row — it doesn't have access to it at all (see section 3).

### Priority 2 — Cash Wallet / Cash Safe (commits `6eea9c2`, `4e0eda5`) — mostly already true, verified
- Added `FinancialAccount.AccountType.CASH_SAFE` ("Cash Safe") alongside
  the existing `CASH` ("Cash Wallet") — additive choices-only change.
- Everything else in this requirement (independent balances/history, all
  six transfer directions, internal transfers excluded from income/expense
  totals, per-account opening balance+date) was **already true** of the
  existing generic multi-account architecture — verified by reading
  `apps/reports/services.py` (income/expense totals come from the
  `Transaction` model only, never `Transfer`/`Borrowing`/`Lending`) and by
  the new `test_transfer_respects_each_accounts_own_cutoff` /
  `test_bulk_account_balances_handles_mixed_cutoff_and_no_cutoff_accounts`
  tests.
- Added `static/js/form-guard.js` (site-wide): disables a submitted form's
  button synchronously so a double-click/tap can't fire a second real
  transfer. New deterministic E2E test confirms only one `Transfer` row is
  created from a simulated double-click.
- **Found and fixed a real, pre-existing, unrelated flaky test** while
  running the full E2E suite for verification: `test_daily_closing_close_
  and_reopen` failed on WebKit due to the exact `networkidle`-timing trap
  `CLAUDE.md` already warns about. Confirmed via `git stash` that it failed
  *before* this phase's changes too. Fixed with `expect_navigation()`.
- **Not done:** the real "Wallet & Safe" account itself has not been split
  into two accounts — the brief explicitly says not to auto-split it until
  the owner specifies how to allocate the balance. Nothing was done here on
  purpose; it's `PENDING — needs approval` (see below), not forgotten.

### Priority 3 — Back buttons (commit `1f3c585`) — DONE
Added `templates/base/_back_link.html` (server-rendered via `{% url %}`,
never `history.back()` — so there's always a sensible destination even
with no browser history) to every create/edit form, delete-confirmation
page, and detail page that previously had no way back except the
browser's own back button — accounts, transfers, transactions, favorites,
borrowings, lendings, bills, budgets, goals, closing-day detail, admin
user detail. Top-level pages (dashboard, each app's list/index) were left
alone — they're one tap away via the sidebar already. Also added
`static/js/back-guard.js`: warns with a native `confirm()` before
discarding a form the user has actually typed into. 4 new E2E tests. Full
E2E suite (72 relevant tests at the time) passed on Chromium, Firefox, and
WebKit.

**Note added after Priority 5 landed:** these 4 tests then failed on all
3 browsers once Priority 5 added a "Help & Feedback" nav link — not an
app bug, a test bug. Playwright's `get_by_role(name="Back")` does
case-insensitive *substring* matching by default, and "feed**back**"
contains "back", so the locator started matching two links. Fixed by
adding `exact=True`; re-verified green on all three engines as part of
the final full-suite run (see section 8).

### Priority 4 — secure registration — CODE ALREADY COMPLETE from Phase 9/10, production toggle is the only remaining step, and it's blocked
No new code was needed — this was built and tested in earlier phases.
Verified this session (not assumed) by reading the code and re-running the
relevant tests:
- Email+password auth, login/logout: `apps/users/views.py` (`EmailLoginView`,
  `EmailLogoutView`) — works.
- Duplicate email: `User.email` has `unique=True` — DB-level, not just
  form-level. `test_duplicate_email_rejected` passes.
- Strong passwords: `AUTH_PASSWORD_VALIDATORS` configured in
  `config/settings/base.py`.
- Brute-force protection: `django-axes`, `AXES_FAILURE_LIMIT=5`,
  `AXES_ENABLED=True` in `config/settings/prod.py`.
  `test_repeated_failed_logins_are_locked_out` passes.
- Isolation: enforced architecturally (see `CLAUDE.md`), `test_isolation.py`
  (7 tests) passes.
- Admin privileges unaffected — nothing here touches `is_staff`.
- Desktop + mobile: `tests/e2e/test_auth_e2e.py::test_registration_login_
  logout_flow` (desktop) + `tests/e2e/test_mobile_viewports_e2e.py::
  test_register_page_has_no_overflow_on_mobile` (320/360/390/430px) —
  both pass, on all three browser engines.

**The actual gate is a single DB row**, not code:
`apps.adminpanel.models.SiteConfiguration.allow_public_registration`
(singleton, `pk=1`). Per Phase 10's own notes, it's currently `False` on
the live site (set there deliberately, overriding the model's own
`default=True`). **This session cannot read or change that row — no
production DB access (see section 3).** This is the real remaining action
for Priority 4, and it's `PENDING — needs approval` below, not something
code can fix.

**SMTP status, reported honestly, not re-verified this session (no
access):** Phase 9 configured SMTP settings but never verified real
delivery (no credentials available then). Phase 10 confirmed SMTP was
*not* configured on the live server at all — password reset would log to
console/nowhere. This session has no way to check whether that's still
true. **Do not enable public registration while this is true**: a new
user who forgets their password would have no way to reset it. Whoever
has VPS access should check `.env` for `SMTP_HOST` etc. before flipping the
registration toggle, or the owner should accept that risk explicitly.

### Priority 5 — Help & Feedback (commit `859c9fd`) — DONE
New `apps.feedback` app: category (Report a Problem / Suggest an
Improvement / Ask for Help), subject, description, optional screenshot,
status (Pending/In Progress/Resolved), admin response — all in-app, works
with zero SMTP since nothing here depends on email. "Help & Feedback" link
added to the shared sidebar/mobile-nav partial. Admin side added to the
existing `apps.adminpanel` app (new "Feedback" tab, filter by category/
status, respond, change status) rather than inventing a separate admin UI.

Security specifically for "do not expose attachments or private
information publicly": screenshots are stored in `private_media/` — a
directory outside `MEDIA_ROOT` that is never served by the webserver
config and has no public URL at all — and are only ever readable through
`feedback:attachment`, which checks the requester is the submission's
owner or staff (404 for anyone else, redirect-to-login for anonymous).
Upload validated server-side for file extension and a 5 MB size cap.
`compose.yaml` gained a bind mount (`PRIVATE_MEDIA_DIR`, same pattern as
the existing Postgres data/backup mounts) so uploads survive a container
recreate in production.

14 new unit tests + 2 new E2E tests, all passing (see section 9).

## 6. Files modified

See the four commits listed in section 2 for exact diffs
(`git show <hash>` / `git log -p`). Not re-listed here file-by-file to
avoid this document drifting out of sync with the actual diff — the commit
messages themselves are detailed and are the source of truth.

## 7. Database migrations created or applied

Both are **created, applied locally against SQLite for the test suite, but
NOT applied anywhere else** (no dev/staging/production Postgres was
reachable or touched this session):

- `apps/accounts/migrations/0003_financialaccount_opening_balance_date_and_more.py`
  — adds `opening_balance_date` (nullable, no default-value backfill
  needed since `null=True`) and widens the `account_type` choices list to
  include `cash_safe`. Neither operation rewrites any existing row's data;
  this is safe to run against the live Postgres database with zero
  observable effect on current balances.
- `apps/feedback/migrations/0001_initial.py` — creates the new
  `FeedbackSubmission` table. Brand new table, cannot conflict with
  anything existing.

Both are reviewable with `python manage.py sqlmigrate <app> <number>`
before running them for real, per the brief's own "review all migrations"
requirement — do that on whatever machine actually has Postgres access.

## 8. Tests executed and results

All executed locally this session (not claimed without running):

- `pytest tests/ --ignore=tests/e2e -q` → **91 passed** (77 pre-existing +
  14 new from Priority 5; Priority 1's 12 new tests and Priority 3's back-
  link additions didn't change the top-level count because Priority 1's
  tests were added before the 77-baseline was taken, i.e. 77 already
  includes them — see the commit-by-commit numbers in each commit message
  if you want the exact running total at each step).
- `pytest tests/e2e/ --browser chromium --browser firefox --browser webkit -q`
  → **138 passed** (final full run, after fixing one test bug described
  below). Run multiple times through this phase as each priority landed.
- Specifically re-verified for Priority 4: `test_registration_and_admin.py`,
  `test_isolation.py`, `test_security.py` → 22/22 passed.
- `python manage.py check` → "System check identified no issues" (run
  after every priority's code changes, not just once).

## 9. GitHub commit hashes

See section 2.

## 10. Production deployment status

**Nothing deployed. Nothing in production touched.** See section 3 for
exactly why (no SSH access to the deployment path with the account this
session had) and exactly what the next session with real access needs to
do.

## 11. Outstanding bugs

None found that aren't already described above. The one pre-existing bug
found (`test_daily_closing_close_and_reopen` WebKit flake) was fixed in
commit `4e0eda5`.

## 12. Known security concerns

- Priority 4's registration toggle being off is a *missing feature*, not a
  vulnerability — leave it off until SMTP is confirmed working or the
  owner explicitly accepts the no-password-reset risk.
- Nothing else new. The existing security posture (django-axes, CSRF,
  JWT validation, isolation) was re-verified, not weakened, by this phase.

## 13. Changes requiring the owner's approval before anything happens to real data

(Mirrors the brief's own explicit gates — nothing below was done.)

1. **Set the real `opening_balance_date`/`opening_balance` on the actual
   "Wallet & Safe" account** (Priority 1) — needs the owner to confirm the
   631,200 / 850,000 / 1,481,200 figures are correctly understood before
   anyone writes to that row.
2. **Decide how to split "Wallet & Safe" into two accounts and how to
   allocate its current balance** (Priority 2) — explicitly deferred by
   the brief itself.
3. **Flip `SiteConfiguration.allow_public_registration` to `True`**
   (Priority 4) — and first confirm SMTP is configured, or accept that
   password reset won't work yet.
4. **Apply the two new migrations to the real production database** and
   redeploy — needs someone with actual VPS/DB access, which this session
   didn't have.

## 14. Exact next commands (for whoever has real VPS/DB access)

```bash
# On the VPS, as the therealworldboosts-money site user:
cd /home/therealworldboosts-money/mymoney-tracker
git fetch && git log origin/master -5   # confirm these 4 commits are there once pushed
git merge origin/master
docker compose exec web python manage.py sqlmigrate accounts 0003   # review first
docker compose exec web python manage.py sqlmigrate feedback 0001
docker compose exec web python manage.py migrate
# add PRIVATE_MEDIA_DIR=/home/therealworldboosts-money/mymoney-tracker/private_media to .env
docker compose up -d --build web
# then re-run the same live smoke checks Phase 10 did against
# https://money.therealworldboosts.com before telling the owner it's live.
```

## 15. Instructions for the next assistant (Claude Code, DeepSeek, or otherwise)

1. Read `CLAUDE.md` first (short, has the one non-obvious architecture
   fact you need before touching money code), then this file.
2. Don't restart anything above — it's done, tested, and committed
   locally. Start from "Changes requiring approval" (section 13) and ask
   the owner directly rather than guessing at the historical balance
   figures or deployment timing.
3. If you get real SSH/DB access that this session didn't have, your
   first move is section 14's commands, not new feature work.
4. Keep updating this file after each milestone, the same way this
   session did — don't let it go stale.
5. No production passwords, keys, or secrets are written here, on
   purpose. None should be.
