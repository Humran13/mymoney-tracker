# Phase 13 Handoff — Financial Accuracy &amp; Dashboard Improvements

**Last updated:** 2026-11-12
**For:** whichever assistant (Claude Code, DeepSeek, or a human) picks this up next.
Read this before touching money code — it records the accounting decisions, the
one real bug that keeps recurring, and what is *implemented* vs *tested* vs
*deployed* vs *applied to the owner's real data*.

---

## 0. Repository state verified at the start of this phase

| Check | Result |
|---|---|
| Repository | https://github.com/Humran13/mymoney-tracker (public, branch `master`) |
| Working checkout used | `D:\mymoney-tracker` |
| `origin/master` before this phase | `78e07ed` — "Phase 12: record production deployment of debt classification feature" |
| Was the previous session's push really pending? | **No.** Both `c537c6c` (feature) and `78e07ed` (docs) were already on `origin/master`. The claim that `78e07ed`'s push was pending was wrong — verified with `git fetch` + `git log origin/master`. |
| Uncommitted local changes | None at the start |
| Stale second checkout | `C:\Users\Humran\Documents\APPS\MYMONEY TRACKER` exists and is stuck at Phase 10 (`04163dd`). **Do not use it** — it is not the working repo. |

This phase started from `78e07ed` and nothing was force-pushed.

---

## 1. The seven requirements — status

| # | Requirement | Code | Tested | Pushed | Deployed | Real data corrected |
|---|---|---|---|---|---|---|
| 1 | Detailed daily income vs expenses analysis (new page, filters, daily + weekly + drill-down) | ✅ | ✅ | ✅ | ❌ pending | n/a |
| 2 | "Last 24 Hours" spending-trend option | ✅ | ✅ | ✅ | ❌ pending | n/a |
| 3 | Charity expense category | ✅ | ✅ | ✅ | ❌ pending | ❌ owner approval needed |
| 4 | Shipping Fees expense category | ✅ | ✅ | ✅ | ❌ pending | ❌ owner approval needed |
| 5 | Optional transaction fee on debt repayments | ✅ | ✅ | ✅ | ❌ pending | ❌ owner approval needed |
| 6 | Monthly income vs expenses comparison on the dashboard | ✅ | ✅ | ✅ | ❌ pending | n/a |
| 7 | Historical debts must not inflate opening balances (+ read-only reconciliation workflow) | ✅ | ✅ | ✅ | ❌ pending | ❌ **explicitly not applied** |

"Deployed: pending" means this session had no SSH/VPS access and did not
pretend otherwise. See §8.

---

## 2. Root cause of the recurring "historical debt inflates my balance" bug

This is the bug the owner has reported repeatedly. It is **two** problems, and
only one of them was fixed in Phase 11.

### 2.1 The mechanism

`apps/accounts/services.account_balance()` is:

```
opening_balance + sum(AccountPosting.amount)   # cut off at opening_balance_date
```

`apps/debts/services._borrowing_postings()` writes one positive posting dated
`date_borrowed` when a debt is a **Cash Loan**. That is correct: a real cash
loan does put money in an account. The posting's date is deliberately the real
borrowing date, not "today", so a loan remembered months later still lands on
its true date.

### 2.2 What Phase 11 fixed

`FinancialAccount.opening_balance_date` plus the cutoff filter in
`account_balance()` / `bulk_account_balances()`: postings dated **before** the
snapshot date are excluded from the balance because they are already inside
`opening_balance`.

### 2.3 What was still broken — the real, current cause

**`opening_balance_date` is `NULL` on every pre-existing account.** Phase 11's
migration was additive only, so no real account ever received a snapshot date,
and the cutoff filter is skipped entirely when the date is NULL. Phase 12's
deploy verification even recorded this explicitly ("the new
`opening_balance_date` column is `NULL` on all of them"). So for the owner's
actual accounts, a September loan still adds its full amount to today's cash:

```
opening_balance 500,000  (real cash on 10 Nov)
+ borrow_receive 1,292,000 dated 1 Sep   ← still summed, because no cutoff
= 1,792,000                             ← wrong; the money was spent long ago
```

A second, quieter gap contributed to it: **onboarding never defaulted the
snapshot date**. `AccountFormSet` in `apps/accounts/views.onboarding` got no
initial value, so accounts created during first-time setup were born with
`opening_balance_date = NULL`. Only the separate "Add account" form
pre-filled today. Every account the owner created through onboarding therefore
started in the broken state.

### 2.4 What this phase does about it (and deliberately does **not** do)

* **Engine stays deterministic.** Nothing about which postings count was
  changed. A loan dated before a snapshot is excluded, dated on/after it is
  included — one definition, used by `account_balance`, `bulk_account_balances`,
  `balance_as_of`, `day_breakdown` and the new cash-flow service.
* **New accounts now get a snapshot date** (form default = today), including
  through onboarding, so new data cannot enter the broken state.
* **Existing NULL-date accounts are surfaced, not silently rewritten.** The
  dashboard warns ("N account(s) have no opening-balance date…") and links to a
  new read-only reconciliation screen
  (`/accounts/reconciliation/`, `apps/accounts/reconciliation.py`).
* **No automatic money correction.** The preview proposes `proposed_balance ==
  current_balance` and `difference == 0.00` on purpose. The only writable
  action is setting/clearing the **date**, after a preview *and* a second
  explicit confirmation, audited via `AuditEvent`, and reversible by clearing
  the date. Opening balances, postings, loans and repayments are never touched.
* **Why:** some real balances were already hand-adjusted to compensate for the
  earlier bug. An automatic "-1,292,000" could subtract the same historical
  loan twice. Only the owner knows which is true for their data.

### 2.5 The acceptance test, pinned exactly

`tests/test_phase13_historical_debt.py::AcceptanceTestHistoricalLoan`

```
account opening_balance 500,000, opening_balance_date 2026-11-10
cash loan 1,292,000 dated 2026-09-01, due 2026-10-31
  → account balance 500,000.00   ✅
  → outstanding     1,292,000.00 ✅
repayment 100,000 on 2026-11-12
  → account balance 400,000.00   ✅
  → outstanding     1,192,000.00 ✅
```

Also pinned: loans entered days/months later behave identically (the *entry*
date never decides), a loan dated on the snapshot date counts once, one day
before counts zero, a repayment before the snapshot reduces debt but not cash,
goods/services on credit never move cash, and the NULL-date legacy behavior is
documented by a test rather than left implicit.

---

## 3. Code changes

### Improvement 1 — Income vs Expenses Analysis
* `apps/reports/cash_flow.py` (**new**) — period resolution + validation,
  cash-in/cash-out totals, per-day rows (zero days included), weekly blocks,
  accrual summary, per-day drill-down. Single-user scoped, `Decimal` only.
* `apps/reports/views.py` — `income_vs_expenses`, `income_vs_expenses_day`.
* `apps/reports/urls.py` — `reports:income_vs_expenses`,
  `reports:income_vs_expenses_day`.
* `templates/reports/income_vs_expenses.html`,
  `templates/reports/income_vs_expenses_day.html` (**new**).
* Default view = **today**; presets Today / Yesterday / specific date / 7 / 30 /
  60 days / custom range, validated (reversed or partial ranges fall back to
  today with a message instead of a 500). Dates use `timezone.localdate()`,
  i.e. `Africa/Kampala`.

### Improvement 2 — Last 24 Hours
* `apps/dashboard/services.py` — `("24h", "Last 24 Hours")` added as the first
  `TREND_PERIOD_CHOICES` entry; all previous options untouched.
* **Honest limitation, stated in the UI**: `Transaction`/`AccountPosting` store
  a calendar `date`, not a timestamp, so a true rolling 24-hour window cannot be
  reconstructed. Rather than fabricate an hourly curve (or misuse `created_at`
  as if it were when the money moved), the 24-hour option reports today's
  calendar-day total, labels the point "Today", and shows
  `TREND_24H_NOTE` under the chart. Preference persistence and Chart.js styling
  are unchanged.
* `templates/dashboard/home.html` — note element + JS that toggles it.

### Improvements 3 &amp; 4 — Charity and Shipping Fees
* `apps/transactions/migrations/0003_default_charity_and_shipping_categories.py`
  (**new**, `RunPython`, idempotent `get_or_create` on `user=None`).
* `apps/transactions/management/commands/seed_categories.py` — both added to
  `EXPENSE_CATEGORIES`.
* No per-user migration is needed: categories with `user=None` are global and
  already visible to every existing and new user. A user's own custom category
  with the same name is left alone (tested).

### Improvement 5 — Transaction fees on debt repayments
* `apps/debts/models.py` — `BorrowingRepayment.fee_amount` (Decimal, default 0)
  and `total_cash_out()`. `Borrowing.receipt_affects_current_balance()` added
  so templates/reports can explain a pre-snapshot loan without re-deriving it.
* `apps/debts/services.py` — `_repayment_postings()` writes the principal
  (`BORROW_REPAY`) and, only if non-zero, exactly one `FEE` posting;
  `record_borrowing_repayment`, `update_borrowing_repayment` (new) and
  `delete_borrowing_repayment` all go through `sync_postings`/`delete_postings`
  inside `transaction.atomic()`. Negative fees and overpayment are rejected at
  service level *and* in the form. `_available_to_repay()` adds the repayment's
  own amount back when editing, so increasing 100k → 150k isn't misread as an
  overpayment.
* `apps/debts/forms.py` — optional `fee_amount` (default 0, `min=0`).
* `apps/debts/views.py` + `urls.py` — `repayment_update`, `repayment_delete`
  (previously a repayment could be created but never edited or deleted).
* `templates/debts/borrowing_detail.html`, `repayment_form.html` (**new**),
  `repayment_delete_confirm.html` (**new**) — fee field, fee shown separately
  from the principal, "cash actually deducted" line, edit/delete links.
* `api/v1/serializers.py` — `fee_amount` exposed/accepted on
  `BorrowingRepaymentSerializer`.
* `apps/reports/services.py` — repayment fees added **once** to
  `total_expenses` (`repayment_fees`, `total_fees` keys added; principal stays
  out of expenses). Existing repayments have `fee_amount = 0`, so every
  pre-Phase-13 total is arithmetically identical.

### Improvement 6 — Monthly comparison on the dashboard
* `apps/dashboard/services.py` — `resolve_month()`, `month_links()`,
  `compute_dashboard(user, month_param)`; one
  `compute_monthly_report()` call feeds both the card and the warning text, so
  they cannot disagree. `health_warnings()` now returns
  `{"text", "url", "link_text"}` dicts.
* `apps/dashboard/views.py` — `?month=YYYY-MM` (invalid values fall back to the
  current month).
* `templates/dashboard/home.html` — side-by-side Income | Expenses, Difference,
  plain-language sentence, month picker with ‹ › navigation, link to the
  analysis page. The old sidebar "This month" mini-card was removed (its
  content now lives in the full card) to avoid showing the same numbers twice.

### Improvement 7 — Reconciliation + snapshot-date defaults
* `apps/accounts/reconciliation.py` (**new**) — `snapshot_balance()`,
  `account_reconciliation()`, `reconciliation_preview()`,
  `apply_opening_snapshot_date()` (atomic, audited, reversible).
* `apps/accounts/views.py` / `urls.py` — `accounts:reconciliation` (GET,
  read-only) and `accounts:opening_snapshot` (POST: preview, then confirm).
* `apps/accounts/forms.py` — `OpeningSnapshotForm`; `FinancialAccountForm` now
  defaults a **new** account's snapshot date to today (covers onboarding, which
  previously left it NULL).
* `templates/accounts/reconciliation.html` (**new**),
  `templates/accounts/onboarding.html` (snapshot field + explanation),
  `templates/accounts/account_list.html` (link).

### Cross-cutting accuracy fix
* `apps/closing/services.py::day_breakdown` — internal transfers no longer
  count as money received/spent (rule 7). They stay visible via `by_type` and
  new `transfers_in`/`transfers_out` keys; a transfer **fee** still counts as
  money out. This corrects the dashboard's "Received today"/"Spent today" tiles
  and the Daily Closing page, which previously inflated both sides by the same
  transfer amount.

---

## 4. Accounting decisions (the rulebook this phase implements)

1. **Two separate statements, never merged.**
   * *Cash movement* = what actually moved in/out of the user's own accounts
     (what the dashboard tiles and the analysis page show).
   * *Income earned / expenses incurred* = accrual (what the monthly report and
     the monthly comparison card show).
   The analysis page displays both in separate blocks with a one-line
   explanation, so a cash loan being "money in" is never read as "income".
2. **Cash loan** → increases cash *only* if its receipt date is inside the
   receiving account's snapshot window; always increases debt; never income.
3. **Historical loan (receipt before the snapshot)** → increases debt only. The
   posting is kept for audit but excluded from the balance by the cutoff.
4. **Goods/services on credit** → increase debt, never touch cash, and never
   create an income transaction.
5. **Debt repayment** → reduces cash and debt by the principal.
6. **Repayment transaction fee** → reduces cash, does **not** reduce debt, and
   appears exactly once as an expense in reporting.
7. **Internal transfer** → not income, not expense; excluded from both sides of
   cash-in/cash-out. Only its fee is a real cost.
8. **Loan principal repaid** is not an extra expense (it settles a liability;
   counting it would double-count the purchase it financed).
9. **Same-day boundary**: `opening_balance` is the money at the **start** of
   `opening_balance_date`; postings dated on that date are added. One
   definition everywhere (verified by tests on both sides of the boundary).
10. **Cash-flow windows respect snapshot dates**: a posting excluded from the
    balance is also excluded from cash-flow figures, so the report always
    explains the balance instead of contradicting it.

---

## 5. Database migrations

| Migration | Type | Effect | Safe for existing data |
|---|---|---|---|
| `debts.0004_borrowingrepayment_fee_amount` | `AddField` | new `DecimalField(default=0)` | ✅ additive; existing rows get `0.00`, i.e. the old behaviour |
| `transactions.0003_default_charity_and_shipping_categories` | `RunPython` | `get_or_create` two global (`user=None`) expense categories | ✅ idempotent; touches no user's rows. Reverse is a deliberate no-op so a rollback can't orphan expenses into "Uncategorized" |

Both were reviewed with `git show`/file reading before applying. Neither is
destructive, neither rewrites money, and `makemigrations --check --dry-run`
reports **no changes detected** after them.

---

## 6. Tests and results (actually executed)

**Unit / integration (SQLite, `pytest tests/ --ignore=tests/e2e -q`)**

```
242 passed
```

Baseline before this phase was 136 passed, so **106 new tests** were added:

| File | Covers |
|---|---|
| `tests/test_phase13_historical_debt.py` | the acceptance test; cases A–E; snapshot boundary; repayments before/after snapshot; NULL-cutoff legacy behavior; read-only preview; audited/reversible apply; per-user isolation |
| `tests/test_phase13_repayment_fees.py` | fee accounting across cash/MTN/Airtel/bank; zero fee; partial & full; edit; fee removal on edit; delete; duplicate-posting prevention; negative fee; overpayment; report double-count; form validation; web create/edit/delete; cross-user 404s; API |
| `tests/test_phase13_cash_flow.py` | period presets & validation; 150k/185k example; zero-activity days; fees; transfers excluded; loan ≠ income; repayment ≠ expense; 7/30/60-day; custom; daily rows sum to totals; weekly blocks; drill-down (no duplication); dashboard "today" tiles |
| `tests/test_phase13_pages.py` | analysis page default/filters/drill-down/isolation; monthly card + clickable warning + month selection; reconciliation page preview/confirm/permissions |
| `tests/test_phase13_trend_and_categories.py` | 24h option, values, note, persistence, isolation, other periods unchanged; Charity & Shipping Fees existence/idempotency/forms/reports/custom-category safety |
| `tests/test_smoke.py` (extended) | the three new pages render |

**Browser (Playwright, real Chromium/Firefox/WebKit)**

```
tests/e2e/ --browser chromium            → 75 passed
tests/e2e/test_phase13_e2e.py --browser firefox → 5 passed
tests/e2e/test_phase13_e2e.py --browser webkit  → 5 passed
tests/e2e/test_mobile_viewports_e2e.py (new 320/360/390/430 cases) → 12 passed (chromium)
```

New E2E coverage: monthly card values + clickable warning → analysis page;
period filter + daily table + drill-down + back link; month selector switching;
recording a repayment with a fee in a real browser; reconciliation page
reachable and read-only; no horizontal overflow on the new pages at 320–430px.

**Lint:** `ruff check .` → *All checks passed* (project's narrow E9/F ruleset).
**Settings:** `manage.py check` → *no issues*.
**CSS:** `npm run build:css` rebuilt after the template changes (the compiled
file is git-ignored and built inside the Docker image).

---

## 7. What still needs the owner's explicit approval (real data)

Nothing below was applied. The app *can* do each one; only the owner can decide.

1. **Set an opening snapshot date for each existing account that has none**
   (`/accounts/reconciliation/`). This is the fix for the reported bug. The page
   shows, per account: opening balance, current snapshot date, historical cash
   loans, historical postings, postings after the snapshot, current calculated
   balance, the projected balance for a candidate date, and the exact
   difference — then requires a second confirmation.
   *Recommended first step:* open the page, read it, and apply a date only for
   accounts whose opening balance was entered as "the cash I actually had on
   <date>".
2. **Do not** expect the reconciliation to change any balance by itself; it
   changes only the *date*. If a balance turns out to be wrong after setting the
   date, that is a separate, explicitly-approved opening-balance edit.
3. Any **reclassification** still pending from Phase 12 (the real Tasha Cargo
   446,000 borrowing, account 6) is untouched by this phase.

---

## 8. Deployment status

**Not deployed.** This session had no SSH/VPS access to
`money.therealworldboosts.com`, so nothing was pushed to production and no
production backup was taken or verified in this session. No claim is made
otherwise.

When access is available, the sequence that has been used successfully in
Phases 10–12 is:

1. Confirm `origin/master` is the intended commit and CI is green.
2. On the VPS: `pg_dump` a fresh backup, `chmod 600`, restore it into an
   isolated `verify_restore` database, compare row counts and `sum(amount)` for
   `users_user`, `accounts_financialaccount`, `accounts_accountposting`,
   `debts_borrowing`, `debts_borrowingrepayment`, then drop `verify_restore`.
3. `git merge --ff-only origin/master` as the site user.
4. `docker compose build web && docker compose up -d --no-deps web` (never
   `down -v`; never restart `db`).
5. Watch the entrypoint log for:
   `Applying debts.0004_borrowingrepayment_fee_amount... OK` and
   `Applying transactions.0003_default_charity_and_shipping_categories... OK`.
6. Re-check that no real account's `opening_balance` changed and that
   `opening_balance_date` values are unchanged (this phase never sets them).
7. Verify live with a **disposable** account, then delete it via
   `delete_user_and_all_data()`.

Post-deploy expected state: every existing account still has
`opening_balance_date = NULL` (unchanged, by design), balances unchanged, and
the dashboard now shows the "no opening-balance date" warning pointing at the
reconciliation screen.

---

## 9. Known limitations

* **"Last 24 Hours" is today's calendar day, not a rolling 24-hour window** —
  transactions store a date, not a timestamp. The UI says so. A true hourly
  window would require adding an occurrence **timestamp** to
  `Transaction`/`AccountPosting` (and a migration/backfill story for existing
  rows); that was deliberately not attempted here because a half-migrated
  timestamp would be more misleading than an honest label.
* **Legacy NULL-date accounts still double-count historical entries** until the
  owner applies a snapshot date. That is intentional (see §2.4), not an
  oversight.
* **Negative amounts render as `UGX -35,000`**, matching the rest of the app's
  `format_money` output rather than the brief's `-UGX 35,000`. Changing the
  global formatter would alter every page.
* **The reconciliation screen proposes no corrected balance** (`difference`
  is always `0.00`) — deliberately, because only the owner knows whether a given
  opening balance was hand-adjusted already.
* **Lending repayments still cannot be edited** (only borrowing repayments can,
  which is what the brief required). `delete_lending_repayment` exists in
  services but is not yet exposed in the UI.
* **The drill-down page shows one day at a time**; the weekly table links are
  not clickable.

---

## 10. Next steps

1. Deploy using §8 (needs VPS access), then smoke-test the three new pages.
2. Owner: walk through `/accounts/reconciliation/` and apply snapshot dates
   where the opening balance genuinely reflected that date. This is what
   actually fixes the reported balance inflation.
3. Owner: decide on the outstanding Phase 12 Tasha Cargo reclassification.
4. If hourly accuracy is wanted later, add an `occurred_at` timestamp to
   `Transaction`/`AccountPosting` (nullable, defaulting to `occurred_on` at
   00:00 so old rows stay honest), and only then turn "Last 24 Hours" into a
   genuine rolling window.
5. Optional cleanups: expose lending-repayment edit/delete; make weekly rows
   clickable; consider whether `apps/core/currency.format_money` should render
   negatives as `-UGX 35,000`.

---

## 11. Quick reference — where things live

| Concern | Location |
|---|---|
| Balance engine | `apps/accounts/services.py` (`account_balance`, `bulk_account_balances`) |
| Posting writer | `apps/accounts/services.py` (`sync_postings`, `delete_postings`) |
| Reconciliation | `apps/accounts/reconciliation.py` + `/accounts/reconciliation/` |
| Deposit/repayment postings | `apps/debts/services.py` (`_borrowing_postings`, `_repayment_postings`) |
| Cash-flow analysis | `apps/reports/cash_flow.py` + `/reports/income-vs-expenses/` |
| Monthly report (accrual) | `apps/reports/services.compute_monthly_report` |
| Daily cash movement | `apps/closing/services.day_breakdown` |
| Dashboard aggregation | `apps/dashboard/services.compute_dashboard` |
| Spending trend | `apps/dashboard/services.spending_trend` |
| Categories | `apps/transactions/models.Category` + `seed_categories` command |
