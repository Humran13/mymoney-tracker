# CLAUDE.md

Guidance for Claude Code (or any future assistant) working in this repo.

## What this is

MyMoney Tracker: a Django monolith personal-finance tracker for Uganda
(UGX). Read `DECISIONS.md` before changing money-handling or auth code —
several non-obvious choices there (the single-ledger architecture, virtual
goal allocations, suspension enforced at the auth-backend level) exist
because an earlier, more naive version of this code failed a test. Read
`PROJECT_STATUS.md` for what's actually done vs. still open.

## Running things

```bash
source .venv/Scripts/activate      # venv already exists at .venv/
pytest tests/ --ignore=tests/e2e    # unit/integration suite, runs against SQLite — no Postgres needed
pytest tests/e2e/ --browser chromium  # real-browser suite (also: firefox, webkit)
python manage.py runserver          # needs a real Postgres; see README for local setup
python manage.py check               # fast sanity check, no DB needed
npm run build:css                   # rebuild static/css/tailwind.css after ANY template class change
```

`config/settings/test.py` (SQLite) is test-only. Dev and prod always use
PostgreSQL — don't "fix" a test failure by making production code
SQLite-specific.

**If you change a template's Tailwind classes, run `npm run build:css`
before testing.** The compiled stylesheet only contains classes Tailwind
saw at build time — a class added to a template has zero effect until
rebuilt. This bit Phase 9 once (a layout fix silently did nothing until
caught by the mobile E2E test) — don't repeat it.

## The one thing to understand before touching money code

**Account balances are never stored as a mutable number.** They are always
`opening_balance + sum(AccountPosting.amount)` for that account
(`apps/accounts/services.py:account_balance`). Every feature that moves
money — income/expense, transfers, borrowing/lending, bill payments — calls
`sync_postings(source_obj, specs, user=...)` which deletes and recreates
that source object's postings atomically. If you add a new money-moving
feature, follow this pattern: write a `services.py` function that calls
`sync_postings`/`delete_postings`, don't touch `FinancialAccount.opening_balance`
directly, and don't add a new "current_balance" field anywhere.

## Isolation is enforced in two places — both matter

1. Web views: every view filters querysets by `user=request.user` and uses
   `get_object_or_404(Model, pk=pk, user=request.user)`.
2. API: `apps/core/api_mixins.UserOwnedQuerysetMixin` scopes viewset
   querysets, **and** `api/v1/serializers.ScopedFieldsMixin` scopes the
   *foreign key choices* inside serializers (e.g. you can't POST a
   `borrowing` id that belongs to someone else). If you add a new
   ViewSet/Serializer pair with a FK to another user-owned model, you need
   both — the viewset mixin alone does not stop a client from referencing
   another user's row by id in a nested field. `tests/test_isolation.py`
   is where this gets caught; extend it when adding new FKs.

## Tests

`tests/` (unit/integration, SQLite) is organized by feature, not by app:
- `test_accounts_and_fees.py` — ledger correctness, mobile money fees, Decimal precision
- `test_debts.py` / `test_financial_edge_cases.py` — borrowing/lending, partial repayment, carry-forward, overpayment guards, zero/negative amounts, race conditions
- `test_bills_budgets_goals.py` — duplicate-payment prevention, budget %, forecast guardrails
- `test_goal_scenarios.py` — the "what if" scenario calculator (reduce spending / increase income / change deadline / combined)
- `test_isolation.py` — cross-user access, web + API
- `test_security.py` — CSRF, login rate limiting (axes), JWT validation
- `test_registration_and_admin.py` — registration, suspension, admin actions, password reset
- `test_smoke.py` — every major page renders; add new URLs here when you add a page

`tests/e2e/` (Playwright, real Chromium/Firefox/WebKit) drives the actual
rendered app, not just Django's test client — this is what catches
template-rendering bugs and CSS/JS layout bugs the unit suite structurally
can't see. `tests/e2e/conftest.py`'s `submit_and_wait()` helper exists
because `page.click() + wait_for_load_state("networkidle")` is unreliable
in WebKit specifically for a click that triggers a navigation — use it (or
wrap in `expect_navigation()`) for any new E2E test that submits a form.

Run `pytest tests/ --ignore=tests/e2e -q` before considering any change to
`apps/accounts`, `apps/transactions`, `apps/debts`, or `apps/goals` done;
add a `tests/e2e/test_mobile_viewports_e2e.py` case too if the change
touches a template's layout.

## Known rough edges

See "Known limitations" in `PROJECT_STATUS.md` — don't rediscover these,
just check there first.
