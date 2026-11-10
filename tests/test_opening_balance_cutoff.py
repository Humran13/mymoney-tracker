"""Regression tests for the opening-balance-date cutoff (Phase 11, Priority 1).

Real production bug: a historical loan dated *before* an account's opening
balance snapshot date was still summed into the current balance, double-
counting money already reflected in the opening balance itself. These tests
pin down the fix in apps/accounts/services.account_balance /
bulk_account_balances: a posting only affects the balance if
occurred_on >= account.opening_balance_date (inclusive on the cutoff date
itself); accounts with opening_balance_date=None keep the old
include-everything behavior for backward compatibility with existing data.
"""

import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import FinancialAccount
from apps.accounts.services import account_balance, bulk_account_balances, create_transfer
from apps.debts.services import create_borrowing, create_lending, record_lending_repayment
from apps.transactions.services import create_transaction, update_transaction
from apps.users.models import User


class OpeningBalanceCutoffTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="owner@example.com", password="StrongPass123!")
        # Mirrors the real production scenario: opening balance snapshot taken
        # 2026-11-09, with loans entered later for October dates before it.
        self.cutoff = datetime.date(2026, 11, 9)
        self.wallet = FinancialAccount.objects.create(
            user=self.user,
            name="Cash Wallet",
            account_type="cash",
            opening_balance=Decimal("631200.00"),
            opening_balance_date=self.cutoff,
        )

    def test_historical_lending_before_cutoff_does_not_affect_balance(self):
        create_lending(
            user=self.user, borrower_name="Alice", amount=Decimal("250000.00"),
            date_lent=datetime.date(2026, 10, 5), source_account=self.wallet,
        )
        create_lending(
            user=self.user, borrower_name="Bob", amount=Decimal("600000.00"),
            date_lent=datetime.date(2026, 10, 20), source_account=self.wallet,
        )
        # This is the exact real bug: balance must stay at the true opening
        # balance, not be reduced by the pre-cutoff lending postings.
        self.assertEqual(account_balance(self.wallet), Decimal("631200.00"))

    def test_historical_borrowing_before_cutoff_does_not_affect_balance(self):
        create_borrowing(
            user=self.user, lender_name="Carol", amount=Decimal("100000.00"),
            date_borrowed=datetime.date(2026, 10, 1), receiving_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("631200.00"))

    def test_current_period_lending_after_cutoff_reduces_balance(self):
        create_lending(
            user=self.user, borrower_name="Dan", amount=Decimal("50000.00"),
            date_lent=datetime.date(2026, 11, 15), source_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("581200.00"))

    def test_current_period_borrowing_after_cutoff_increases_balance(self):
        create_borrowing(
            user=self.user, lender_name="Eve", amount=Decimal("20000.00"),
            date_borrowed=datetime.date(2026, 11, 20), receiving_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("651200.00"))

    def test_repayment_received_after_cutoff_for_a_historical_loan_affects_balance(self):
        lending = create_lending(
            user=self.user, borrower_name="Alice", amount=Decimal("250000.00"),
            date_lent=datetime.date(2026, 10, 5), source_account=self.wallet,
        )
        # The loan itself predates the cutoff (no balance effect), but a
        # repayment received after the cutoff must still land in the account.
        record_lending_repayment(
            user=self.user, lending=lending, principal_amount=Decimal("100000.00"),
            interest_amount=Decimal("0.00"), date=datetime.date(2026, 11, 12),
            receiving_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("731200.00"))
        # And the debt ledger (independent of the cash-account cutoff) must
        # still correctly show the outstanding balance.
        self.assertEqual(lending.outstanding(), Decimal("150000.00"))

    def test_repayment_before_cutoff_for_a_historical_loan_does_not_affect_balance(self):
        lending = create_lending(
            user=self.user, borrower_name="Alice", amount=Decimal("250000.00"),
            date_lent=datetime.date(2026, 10, 5), source_account=self.wallet,
        )
        record_lending_repayment(
            user=self.user, lending=lending, principal_amount=Decimal("100000.00"),
            interest_amount=Decimal("0.00"), date=datetime.date(2026, 10, 25),
            receiving_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("631200.00"))

    def test_transaction_on_cutoff_date_itself_counts(self):
        create_transaction(
            user=self.user, account=self.wallet, kind="income", category=None,
            amount=Decimal("10000.00"), fee_amount=Decimal("0.00"), date=self.cutoff, description="Same-day income",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("641200.00"))

    def test_transaction_day_before_cutoff_does_not_count(self):
        create_transaction(
            user=self.user, account=self.wallet, kind="income", category=None,
            amount=Decimal("10000.00"), fee_amount=Decimal("0.00"),
            date=self.cutoff - datetime.timedelta(days=1), description="Day before",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("631200.00"))

    def test_editing_a_historical_transaction_does_not_corrupt_current_balance(self):
        txn = create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("30000.00"), fee_amount=Decimal("0.00"),
            date=datetime.date(2026, 10, 10), description="Old expense",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("631200.00"))
        update_transaction(
            txn, account=self.wallet, kind="expense", category=None,
            amount=Decimal("45000.00"), fee_amount=Decimal("0.00"),
            date=datetime.date(2026, 10, 11), description="Old expense (corrected amount)",
        )
        # Still before the cutoff after the edit -> still excluded.
        self.assertEqual(account_balance(self.wallet), Decimal("631200.00"))

    def test_transfer_respects_each_accounts_own_cutoff(self):
        # Bank has no cutoff set (legacy/no-snapshot account) — it should
        # keep including every posting, same as before this fix.
        bank = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("0.00"),
        )
        create_transfer(
            user=self.user, from_account=self.wallet, to_account=bank,
            amount=Decimal("20000.00"), fee_amount=Decimal("0.00"),
            occurred_on=datetime.date(2026, 10, 15),
        )
        # Pre-cutoff transfer out of the wallet: no effect on the wallet.
        self.assertEqual(account_balance(self.wallet), Decimal("631200.00"))
        # Bank has no cutoff, so the same transfer's inbound leg still counts.
        self.assertEqual(account_balance(bank), Decimal("20000.00"))

        create_transfer(
            user=self.user, from_account=self.wallet, to_account=bank,
            amount=Decimal("15000.00"), fee_amount=Decimal("0.00"),
            occurred_on=datetime.date(2026, 11, 10),
        )
        # Post-cutoff transfer: affects the wallet as expected.
        self.assertEqual(account_balance(self.wallet), Decimal("616200.00"))
        self.assertEqual(account_balance(bank), Decimal("35000.00"))

    def test_bulk_account_balances_handles_mixed_cutoff_and_no_cutoff_accounts(self):
        bank = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("500000.00"),
        )
        create_lending(
            user=self.user, borrower_name="Alice", amount=Decimal("250000.00"),
            date_lent=datetime.date(2026, 10, 5), source_account=self.wallet,
        )
        create_transaction(
            user=self.user, account=bank, kind="income", category=None,
            amount=Decimal("10000.00"), fee_amount=Decimal("0.00"),
            date=datetime.date(2026, 1, 1), description="Old income, no cutoff on this account",
        )
        balances = bulk_account_balances(self.user, [self.wallet, bank])
        self.assertEqual(balances[self.wallet.id], Decimal("631200.00"))
        self.assertEqual(balances[bank.id], Decimal("510000.00"))

    def test_no_cutoff_account_keeps_legacy_include_everything_behavior(self):
        # opening_balance_date=None (the migration default for every
        # pre-existing account) must reproduce the exact old behavior:
        # every posting ever recorded affects the balance, regardless of date.
        legacy = FinancialAccount.objects.create(
            user=self.user, name="Legacy Savings", account_type="savings", opening_balance=Decimal("1000.00"),
        )
        create_transaction(
            user=self.user, account=legacy, kind="income", category=None,
            amount=Decimal("500.00"), fee_amount=Decimal("0.00"),
            date=datetime.date(2000, 1, 1), description="Ancient income",
        )
        self.assertEqual(account_balance(legacy), Decimal("1500.00"))
