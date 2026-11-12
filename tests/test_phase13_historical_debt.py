"""Phase 13, improvement 7 — historical debts must not inflate balances.

The exact scenario the owner reported: an account created with real available
cash (opening balance snapshot), plus an old cash loan whose true borrowing
date predates that snapshot. The debt must increase; the cash must not.

All money assertions use `Decimal` and exact expected values.
"""

import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import FinancialAccount
from apps.accounts.reconciliation import (
    apply_opening_snapshot_date,
    reconciliation_preview,
)
from apps.accounts.services import account_balance
from apps.core.models import AuditEvent
from apps.debts.models import Borrowing
from apps.debts.services import create_borrowing, record_borrowing_repayment
from apps.transactions.services import create_transaction
from apps.users.models import User

SNAPSHOT = datetime.date(2026, 11, 10)
LOAN_DATE = datetime.date(2026, 9, 1)
DUE_DATE = datetime.date(2026, 10, 31)
REPAYMENT_DATE = datetime.date(2026, 11, 12)


class AcceptanceTestHistoricalLoan(TestCase):
    """The CRITICAL acceptance test from the brief, verbatim."""

    def setUp(self):
        self.user = User.objects.create_user(email="accept@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user,
            name="Cash Wallet",
            account_type="cash",
            opening_balance=Decimal("500000.00"),
            opening_balance_date=SNAPSHOT,
        )

    def test_historical_loan_then_repayment(self):
        borrowing = create_borrowing(
            user=self.user,
            lender_name="Old Loan",
            amount=Decimal("1292000.00"),
            date_borrowed=LOAN_DATE,
            due_date=DUE_DATE,
            receiving_account=self.wallet,
        )

        # Entering the loan (today) must not move cash that was received and
        # possibly spent months before the snapshot.
        self.assertEqual(account_balance(self.wallet), Decimal("500000.00"))
        self.assertEqual(borrowing.outstanding(), Decimal("1292000.00"))

        record_borrowing_repayment(
            user=self.user,
            borrowing=borrowing,
            amount=Decimal("100000.00"),
            date=REPAYMENT_DATE,
            paying_account=self.wallet,
        )

        self.assertEqual(account_balance(self.wallet), Decimal("400000.00"))
        self.assertEqual(borrowing.outstanding(), Decimal("1192000.00"))

    def test_original_borrowing_and_due_dates_are_preserved(self):
        borrowing = create_borrowing(
            user=self.user,
            lender_name="Old Loan",
            amount=Decimal("1292000.00"),
            date_borrowed=LOAN_DATE,
            due_date=DUE_DATE,
            receiving_account=self.wallet,
        )
        self.assertEqual(borrowing.date_borrowed, LOAN_DATE)
        self.assertEqual(borrowing.due_date, DUE_DATE)

    def test_record_creation_date_never_decides_whether_cash_increases(self):
        # The loan is created *today* but its real receipt was months earlier.
        # Created-after-the-fact must behave exactly like created on the day.
        borrowing = create_borrowing(
            user=self.user,
            lender_name="Old Loan",
            amount=Decimal("1292000.00"),
            date_borrowed=LOAN_DATE,
            receiving_account=self.wallet,
        )
        self.assertGreater(borrowing.created_at.date(), LOAN_DATE)
        self.assertEqual(account_balance(self.wallet), Decimal("500000.00"))
        self.assertFalse(borrowing.receipt_affects_current_balance())


class LoanClassificationCases(TestCase):
    """Cases A-E from the brief, each pinned to exact balances."""

    def setUp(self):
        self.user = User.objects.create_user(email="cases@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user,
            name="Wallet",
            account_type="cash",
            opening_balance=Decimal("500000.00"),
            opening_balance_date=SNAPSHOT,
        )

    def test_case_a_historical_cash_loan(self):
        create_borrowing(
            user=self.user, lender_name="A", amount=Decimal("1292000.00"),
            date_borrowed=LOAN_DATE, receiving_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("500000.00"))

    def test_case_b_new_cash_loan_increases_cash_but_not_income(self):
        create_borrowing(
            user=self.user, lender_name="B", amount=Decimal("200000.00"),
            date_borrowed=REPAYMENT_DATE, receiving_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("700000.00"))
        self.assertEqual(self.user.transactions.count(), 0)

    def test_case_c_goods_on_credit_leaves_cash_untouched(self):
        borrowing = create_borrowing(
            user=self.user, lender_name="C", amount=Decimal("300000.00"),
            date_borrowed=REPAYMENT_DATE, debt_type=Borrowing.DebtType.GOODS_CREDIT,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("500000.00"))
        self.assertEqual(borrowing.outstanding(), Decimal("300000.00"))

    def test_case_d_services_on_credit_leaves_cash_untouched(self):
        create_borrowing(
            user=self.user, lender_name="D", amount=Decimal("120000.00"),
            date_borrowed=REPAYMENT_DATE, debt_type=Borrowing.DebtType.SERVICES_CREDIT,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("500000.00"))

    def test_case_e_repayment_after_snapshot_reduces_cash_and_debt(self):
        borrowing = create_borrowing(
            user=self.user, lender_name="E", amount=Decimal("1292000.00"),
            date_borrowed=LOAN_DATE, receiving_account=self.wallet,
        )
        record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            date=REPAYMENT_DATE, paying_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("400000.00"))
        self.assertEqual(borrowing.outstanding(), Decimal("1192000.00"))

    def test_repayment_before_snapshot_reduces_debt_but_not_cash(self):
        borrowing = create_borrowing(
            user=self.user, lender_name="E2", amount=Decimal("300000.00"),
            date_borrowed=LOAN_DATE, receiving_account=self.wallet,
        )
        record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            date=datetime.date(2026, 10, 15), paying_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("500000.00"))
        self.assertEqual(borrowing.outstanding(), Decimal("200000.00"))

    def test_loan_on_the_snapshot_date_itself_counts_once(self):
        # The snapshot is the START of that day, so a receipt dated on it is
        # inside the window and must be added exactly once.
        create_borrowing(
            user=self.user, lender_name="SameDay", amount=Decimal("50000.00"),
            date_borrowed=SNAPSHOT, receiving_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("550000.00"))

    def test_loan_one_day_before_snapshot_does_not_count(self):
        create_borrowing(
            user=self.user, lender_name="DayBefore", amount=Decimal("50000.00"),
            date_borrowed=SNAPSHOT - datetime.timedelta(days=1), receiving_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("500000.00"))


class NullCutoffAndReconciliationTests(TestCase):
    """Existing accounts with `opening_balance_date IS NULL` (every account in
    production before Phase 11) keep the legacy include-everything behavior —
    which is precisely why those accounts need the read-only review workflow."""

    def setUp(self):
        self.user = User.objects.create_user(email="legacy@example.com", password="StrongPass123!")
        self.legacy = FinancialAccount.objects.create(
            user=self.user,
            name="WALLET",
            account_type="cash",
            opening_balance=Decimal("500000.00"),
        )
        self.loan_date = datetime.date.today() - datetime.timedelta(days=90)

    def _make_historical_loan(self):
        return create_borrowing(
            user=self.user,
            lender_name="Remembered Later",
            amount=Decimal("1292000.00"),
            date_borrowed=self.loan_date,
            due_date=self.loan_date + datetime.timedelta(days=60),
            receiving_account=self.legacy,
        )

    def test_null_cutoff_still_double_counts_the_historical_loan(self):
        # Documents the bug, not the fix: with no snapshot date there is no
        # cutoff to exclude the pre-tracking receipt.
        self._make_historical_loan()
        self.assertEqual(account_balance(self.legacy), Decimal("1792000.00"))

    def test_preview_is_read_only(self):
        borrowing = self._make_historical_loan()
        before_balance = account_balance(self.legacy)
        before_postings = self.legacy.postings.count()
        before_opening = self.legacy.opening_balance

        preview = reconciliation_preview(self.user)

        self.assertEqual(preview["needs_review_count"], 1)
        row = preview["accounts"][0]
        self.assertGreaterEqual(len(row["historical_cash_loans"]), 1)
        self.assertEqual(row["current_balance"], before_balance)
        # Nothing is proposed automatically.
        self.assertEqual(row["proposed_balance"], before_balance)
        self.assertEqual(row["proposed_difference"], Decimal("0.00"))
        self.assertEqual(account_balance(self.legacy), before_balance)
        self.assertEqual(self.legacy.postings.count(), before_postings)
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.opening_balance, before_opening)
        self.assertIsNone(self.legacy.opening_balance_date)
        self.assertFalse(AuditEvent.objects.filter(object_id=str(self.legacy.pk)).exists())
        self.assertEqual(Borrowing.objects.get(pk=borrowing.pk).amount, Decimal("1292000.00"))

    def test_apply_snapshot_date_fixes_the_balance_and_is_audited(self):
        borrowing = self._make_historical_loan()
        result = apply_opening_snapshot_date(account=self.legacy, new_date=SNAPSHOT, actor=self.user)

        self.assertEqual(result["balance_before"], Decimal("1792000.00"))
        self.assertEqual(result["balance_after"], Decimal("500000.00"))
        self.assertEqual(result["difference"], Decimal("-1292000.00"))
        self.assertEqual(account_balance(self.legacy), Decimal("500000.00"))
        # The debt is untouched, and so is the loan record itself.
        self.assertEqual(borrowing.outstanding(), Decimal("1292000.00"))
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.opening_balance, Decimal("500000.00"))
        self.assertEqual(self.legacy.opening_balance_date, SNAPSHOT)
        event = AuditEvent.objects.filter(object_id=str(self.legacy.pk), object_type="FinancialAccount").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.metadata["balance_before"], "1792000.00")
        self.assertEqual(event.metadata["balance_after"], "500000.00")

    def test_apply_then_repayment_matches_the_acceptance_test(self):
        borrowing = self._make_historical_loan()
        apply_opening_snapshot_date(account=self.legacy, new_date=SNAPSHOT, actor=self.user)
        record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            date=REPAYMENT_DATE, paying_account=self.legacy,
        )
        self.assertEqual(account_balance(self.legacy), Decimal("400000.00"))
        self.assertEqual(borrowing.outstanding(), Decimal("1192000.00"))

    def test_apply_is_reversible(self):
        self._make_historical_loan()
        apply_opening_snapshot_date(account=self.legacy, new_date=SNAPSHOT, actor=self.user)
        self.assertEqual(account_balance(self.legacy), Decimal("500000.00"))
        # Clearing the date restores the legacy behavior, exactly as before.
        apply_opening_snapshot_date(account=self.legacy, new_date=None, actor=self.user)
        self.assertEqual(account_balance(self.legacy), Decimal("1792000.00"))

    def test_preview_is_isolated_per_user(self):
        other = User.objects.create_user(email="other-legacy@example.com", password="StrongPass123!")
        FinancialAccount.objects.create(
            user=other, name="Their Wallet", account_type="cash", opening_balance=Decimal("999.00")
        )
        preview = reconciliation_preview(self.user)
        self.assertEqual([row["account"].name for row in preview["accounts"]], ["WALLET"])

    def test_existing_transactions_before_snapshot_are_excluded_too(self):
        create_transaction(
            user=self.user, account=self.legacy, kind="expense", category=None,
            amount=Decimal("70000.00"), fee_amount=Decimal("0.00"),
            date=self.loan_date, description="Pre-tracking spend",
        )
        self.assertEqual(account_balance(self.legacy), Decimal("430000.00"))
        apply_opening_snapshot_date(account=self.legacy, new_date=SNAPSHOT, actor=self.user)
        self.assertEqual(account_balance(self.legacy), Decimal("500000.00"))
