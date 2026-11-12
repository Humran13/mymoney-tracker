"""The permanent fix for the reported bug.

FUNDAMENTAL RULE: the starting balance a user enters is what they actually had.
Everything dated before they started is already inside that number, so recording
an old debt must never increase the money they have today.

The boundary is the account's declared `opening_balance_date`, or — when none
was ever declared — the day the account row was created. No data migration, no
user action, no guessing: the day the balance was typed is a recorded fact.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import FinancialAccount
from apps.accounts.reconciliation import account_reconciliation, reconciliation_preview
from apps.accounts.services import account_balance, bulk_account_balances, create_transfer
from apps.closing.services import balance_as_of, day_breakdown
from apps.dashboard.services import compute_dashboard
from apps.debts.models import Borrowing
from apps.debts.services import create_borrowing, record_borrowing_repayment
from apps.reports.cash_flow import accrual_summary, day_detail, period_cash_flow
from apps.transactions.services import create_transaction, delete_transaction, update_transaction
from apps.users.models import User

TODAY = timezone.localdate()
MONTHS_AGO = TODAY - datetime.timedelta(days=90)
STARTING_BALANCE = Decimal("500000.00")
OLD_LOAN = Decimal("1292000.00")


class HeadlineScenarioTests(TestCase):
    """The exact scenario from the report, on an account that never had an
    opening_balance_date (i.e. every real production account)."""

    def setUp(self):
        self.user = User.objects.create_user(email="headline@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Cash Wallet", account_type="cash", opening_balance=STARTING_BALANCE,
        )

    def _old_loan(self):
        return create_borrowing(
            user=self.user, lender_name="Old Loan", amount=OLD_LOAN,
            date_borrowed=MONTHS_AGO, due_date=MONTHS_AGO + datetime.timedelta(days=60),
            receiving_account=self.wallet,
        )

    def test_starting_balance_is_not_increased_by_an_old_debt(self):
        self.assertIsNone(self.wallet.opening_balance_date)
        borrowing = self._old_loan()

        self.assertEqual(account_balance(self.wallet), STARTING_BALANCE)
        self.assertEqual(borrowing.outstanding(), OLD_LOAN)

    def test_repayment_after_start_reduces_cash_and_debt(self):
        borrowing = self._old_loan()
        record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            date=TODAY, paying_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("400000.00"))
        self.assertEqual(borrowing.outstanding(), Decimal("1192000.00"))

    def test_starting_balance_value_is_never_modified(self):
        borrowing = self._old_loan()
        record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            date=TODAY, paying_account=self.wallet,
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.opening_balance, STARTING_BALANCE)
        self.assertIsNone(self.wallet.opening_balance_date)


class DebtAndCashRulesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="rules@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=STARTING_BALANCE,
        )

    def test_new_loan_after_start_does_increase_cash(self):
        create_borrowing(
            user=self.user, lender_name="New Lender", amount=Decimal("200000.00"),
            date_borrowed=TODAY, receiving_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("700000.00"))
        self.assertEqual(accrual_summary(self.user, TODAY, TODAY)["income"], Decimal("0.00"))

    def test_goods_on_credit_never_moves_cash(self):
        borrowing = create_borrowing(
            user=self.user, lender_name="Supplier", amount=Decimal("400000.00"),
            date_borrowed=MONTHS_AGO, debt_type=Borrowing.DebtType.GOODS_CREDIT,
        )
        self.assertEqual(account_balance(self.wallet), STARTING_BALANCE)
        self.assertEqual(borrowing.outstanding(), Decimal("400000.00"))

    def test_services_on_credit_never_moves_cash(self):
        create_borrowing(
            user=self.user, lender_name="Plumber", amount=Decimal("80000.00"),
            date_borrowed=MONTHS_AGO, debt_type=Borrowing.DebtType.SERVICES_CREDIT,
        )
        self.assertEqual(account_balance(self.wallet), STARTING_BALANCE)

    def test_repayment_fee_is_deducted_exactly_once(self):
        borrowing = create_borrowing(
            user=self.user, lender_name="Lender", amount=Decimal("300000.00"),
            date_borrowed=MONTHS_AGO, receiving_account=self.wallet,
        )
        record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            fee_amount=Decimal("1500.00"), date=TODAY, paying_account=self.wallet,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("398500.00"))
        self.assertEqual(borrowing.outstanding(), Decimal("200000.00"))
        cash = period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["spent"], Decimal("101500.00"))
        self.assertEqual(cash["fee_total"], Decimal("1500.00"))
        self.assertEqual(accrual_summary(self.user, TODAY, TODAY)["expenses"], Decimal("1500.00"))

    def test_transaction_fee_is_deducted_exactly_once(self):
        create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("50000.00"), fee_amount=Decimal("1000.00"), date=TODAY, description="Shopping",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("449000.00"))

    def test_transfer_creates_no_income_or_expense(self):
        bank = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("0.00"),
        )
        create_transfer(
            user=self.user, from_account=self.wallet, to_account=bank,
            amount=Decimal("100000.00"), fee_amount=Decimal("0.00"), occurred_on=TODAY,
        )
        self.assertEqual(account_balance(self.wallet), Decimal("400000.00"))
        self.assertEqual(account_balance(bank), Decimal("100000.00"))
        cash = period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual((cash["received"], cash["spent"]), (Decimal("0.00"), Decimal("0.00")))
        accrual = accrual_summary(self.user, TODAY, TODAY)
        self.assertEqual((accrual["income"], accrual["expenses"]), (Decimal("0.00"), Decimal("0.00")))

    def test_transfer_between_accounts_with_different_start_dates(self):
        old = FinancialAccount.objects.create(
            user=self.user, name="Old", account_type="bank", opening_balance=Decimal("1000.00"),
            opening_balance_date=MONTHS_AGO,
        )
        create_transfer(
            user=self.user, from_account=self.wallet, to_account=old,
            amount=Decimal("20000.00"), fee_amount=Decimal("0.00"), occurred_on=MONTHS_AGO,
        )
        # The wallet didn't exist then, so its side is history only; the old
        # account was already running, so its side counts.
        self.assertEqual(account_balance(self.wallet), STARTING_BALANCE)
        self.assertEqual(account_balance(old), Decimal("21000.00"))


class EditDeleteAndBackdateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="edits@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=STARTING_BALANCE,
        )

    def test_editing_an_in_window_transaction_recalculates(self):
        txn = create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("10000.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Lunch",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("490000.00"))
        update_transaction(
            txn, account=self.wallet, kind="expense", category=None,
            amount=Decimal("25000.00"), fee_amount=Decimal("0.00"), date=TODAY,
            description="Lunch (corrected)", notes="",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("475000.00"))

    def test_deleting_reverses_the_effect(self):
        txn = create_transaction(
            user=self.user, account=self.wallet, kind="income", category=None,
            amount=Decimal("5000.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Gift",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("505000.00"))
        delete_transaction(txn)
        self.assertEqual(account_balance(self.wallet), STARTING_BALANCE)

    def test_backdating_before_the_start_does_not_change_the_balance(self):
        # Deliberate: the starting balance already includes anything earlier.
        create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("70000.00"), fee_amount=Decimal("0.00"), date=MONTHS_AGO,
            description="Backdated spend",
        )
        self.assertEqual(account_balance(self.wallet), STARTING_BALANCE)

    def test_backdating_to_today_counts(self):
        create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("70000.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Today",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("430000.00"))


class StartingDateSourceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="source@example.com", password="StrongPass123!")

    def test_declared_date_beats_the_derived_one(self):
        account = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("1000.00"),
            opening_balance_date=MONTHS_AGO,
        )
        self.assertEqual(account.balance_start_date, MONTHS_AGO)
        self.assertTrue(account.balance_start_is_declared)
        create_transaction(
            user=self.user, account=account, kind="income", category=None,
            amount=Decimal("500.00"), fee_amount=Decimal("0.00"), date=MONTHS_AGO, description="Sale",
        )
        self.assertEqual(account_balance(account), Decimal("1500.00"))

    def test_derived_date_is_the_day_the_account_was_added(self):
        account = FinancialAccount.objects.create(
            user=self.user, name="MTN", account_type="mobile_money_mtn", opening_balance=Decimal("0.00"),
        )
        self.assertEqual(account.balance_start_date, TODAY)
        self.assertFalse(account.balance_start_is_declared)
        self.assertIsNotNone(account.created_at)

    def test_a_posting_on_the_start_date_itself_counts_once(self):
        # Documented boundary: the balance describes the start of that day, so
        # same-day entries are added on top (and are what the user is told).
        account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("1000.00"),
            opening_balance_date=TODAY,
        )
        create_transaction(
            user=self.user, account=account, kind="income", category=None,
            amount=Decimal("200.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Same day",
        )
        self.assertEqual(account_balance(account), Decimal("1200.00"))

    def test_every_account_type_uses_the_same_rule(self):
        for index, (name, account_type) in enumerate(
            [
                ("Cash", "cash"),
                ("Safe", "cash_safe"),
                ("Bank", "bank"),
                ("MTN", "mobile_money_mtn"),
                ("Airtel", "mobile_money_airtel"),
                ("Savings", "savings"),
                ("Other", "other"),
            ]
        ):
            account = FinancialAccount.objects.create(
                user=self.user, name=name, account_type=account_type, opening_balance=Decimal("1000.00"),
            )
            create_borrowing(
                user=self.user, lender_name=f"Old {index}", amount=Decimal("99999.00"),
                date_borrowed=MONTHS_AGO, receiving_account=account,
            )
            self.assertEqual(account_balance(account), Decimal("1000.00"), name)


class ConsistencyAcrossSurfacesTests(TestCase):
    """Every surface must agree, because they now share one definition."""

    def setUp(self):
        self.user = User.objects.create_user(email="consistent@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=STARTING_BALANCE,
        )
        create_borrowing(
            user=self.user, lender_name="Old Loan", amount=OLD_LOAN,
            date_borrowed=MONTHS_AGO, receiving_account=self.wallet,
        )
        create_transaction(
            user=self.user, account=self.wallet, kind="income", category=None,
            amount=Decimal("250000.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Sale",
        )
        create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("50000.00"), fee_amount=Decimal("1000.00"), date=TODAY, description="Stock",
        )

    def test_bulk_matches_single_account_balance(self):
        balances = bulk_account_balances(self.user)
        self.assertEqual(balances[self.wallet.id], account_balance(self.wallet))
        self.assertEqual(balances[self.wallet.id], Decimal("699000.00"))

    def test_dashboard_total_uses_the_same_boundary(self):
        dashboard = compute_dashboard(self.user)
        self.assertEqual(dashboard["total_balance"], Decimal("699000.00"))

    def test_daily_closing_matches(self):
        breakdown = day_breakdown(self.user, TODAY)
        self.assertEqual(breakdown["received"], Decimal("250000.00"))
        self.assertEqual(breakdown["spent"], Decimal("51000.00"))
        self.assertEqual(breakdown["closing_balance"], Decimal("699000.00"))

    def test_balance_as_of_today_matches(self):
        self.assertEqual(balance_as_of(self.user, TODAY), Decimal("699000.00"))

    def test_cash_flow_page_matches(self):
        cash = period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["received"], Decimal("250000.00"))
        self.assertEqual(cash["spent"], Decimal("51000.00"))
        detail = day_detail(self.user, TODAY)
        self.assertEqual(detail["received"], Decimal("250000.00"))

    def test_starting_balance_screen_agrees_and_needs_no_action(self):
        row = account_reconciliation(self.wallet)
        self.assertEqual(row["current_balance"], Decimal("699000.00"))
        self.assertEqual(row["start_date"], TODAY)
        self.assertFalse(row["start_is_declared"])
        self.assertEqual(row["historical_cash_loans"][0].lender_name, "Old Loan")
        self.assertEqual(row["proposed_difference"], Decimal("0.00"))

    def test_user_data_is_isolated(self):
        other = User.objects.create_user(email="isolated@example.com", password="StrongPass123!")
        other_account = FinancialAccount.objects.create(
            user=other, name="Theirs", account_type="cash", opening_balance=Decimal("42.00"),
        )
        create_transaction(
            user=other, account=other_account, kind="income", category=None,
            amount=Decimal("777.00"), fee_amount=Decimal("0.00"), date=TODAY, description="theirs",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("699000.00"))
        self.assertEqual(account_balance(other_account), Decimal("819.00"))
        self.assertEqual([r["account"].name for r in reconciliation_preview(self.user)["accounts"]], ["Wallet"])


class AmbiguityReportingTests(TestCase):
    """Where records genuinely cannot settle the question, the screen says so
    instead of inventing a date or changing money."""

    def setUp(self):
        self.user = User.objects.create_user(email="ambiguity@example.com", password="StrongPass123!")

    def test_zero_starting_balance_with_earlier_history_is_flagged(self):
        account = FinancialAccount.objects.create(
            user=self.user, name="Card", account_type="other", opening_balance=Decimal("0.00"),
        )
        create_transaction(
            user=self.user, account=account, kind="expense", category=None,
            amount=Decimal("30000.00"), fee_amount=Decimal("0.00"), date=MONTHS_AGO, description="Old spend",
        )
        row = account_reconciliation(account)
        self.assertTrue(row["has_history_before_start"])
        self.assertTrue(any("starting balance is 0" in note for note in row["ambiguity"]))
        # Reported, never applied.
        self.assertEqual(account_balance(account), Decimal("0.00"))

    def test_edited_account_is_flagged_but_not_touched(self):
        from apps.core.audit import log_audit_event

        account = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=Decimal("1000.00"),
        )
        log_audit_event(self.user, "update", obj=account, description="Updated account")
        row = account_reconciliation(account)
        self.assertTrue(any("edited since it was created" in note for note in row["ambiguity"]))
        self.assertEqual(account_balance(account), Decimal("1000.00"))

    def test_no_ambiguity_for_a_plain_account(self):
        account = FinancialAccount.objects.create(
            user=self.user, name="Clean", account_type="cash", opening_balance=Decimal("1000.00"),
        )
        self.assertEqual(account_reconciliation(account)["ambiguity"], [])
