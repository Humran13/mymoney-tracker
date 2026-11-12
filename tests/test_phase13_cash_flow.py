"""Phase 13, improvement 1 — daily income vs expenses analysis.

Money in / money out must be cash movement (not accrual), must exclude
internal transfers, and must never count the same transaction twice.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import FinancialAccount
from apps.accounts.services import create_transfer
from apps.debts.services import create_borrowing, record_borrowing_repayment
from apps.reports import cash_flow
from apps.transactions.models import Category
from apps.transactions.services import create_transaction
from apps.users.models import User

TODAY = timezone.localdate()


class PeriodResolutionTests(TestCase):
    def test_default_is_today(self):
        period = cash_flow.resolve_period(None, today=TODAY)
        self.assertEqual(period["preset"], "today")
        self.assertEqual(period["start"], TODAY)
        self.assertEqual(period["end"], TODAY)
        self.assertTrue(period["is_single_day"])

    def test_yesterday(self):
        period = cash_flow.resolve_period("yesterday", today=TODAY)
        self.assertEqual(period["start"], TODAY - datetime.timedelta(days=1))

    def test_specific_date(self):
        chosen = TODAY - datetime.timedelta(days=4)
        period = cash_flow.resolve_period("date", on=chosen, today=TODAY)
        self.assertEqual(period["start"], chosen)
        self.assertEqual(period["end"], chosen)

    def test_future_date_falls_back_with_a_message(self):
        period = cash_flow.resolve_period("date", on=TODAY + datetime.timedelta(days=1), today=TODAY)
        self.assertEqual(period["start"], TODAY)
        self.assertIsNotNone(period["error"])

    def test_7_30_60_day_windows(self):
        for preset, days in (("7d", 7), ("30d", 30), ("60d", 60)):
            period = cash_flow.resolve_period(preset, today=TODAY)
            self.assertEqual(period["days"], days, preset)
            self.assertEqual(period["end"], TODAY)

    def test_custom_range_is_used_when_valid(self):
        start = datetime.date(2026, 1, 1)
        end = datetime.date(2026, 1, 10)
        period = cash_flow.resolve_period("custom", start=start, end=end, today=TODAY)
        self.assertEqual((period["start"], period["end"]), (start, end))
        self.assertIsNone(period["error"])

    def test_custom_range_reversed_is_rejected(self):
        period = cash_flow.resolve_period(
            "custom", start=datetime.date(2026, 5, 1), end=datetime.date(2026, 1, 1), today=TODAY
        )
        self.assertEqual(period["start"], TODAY)
        self.assertIsNotNone(period["error"])

    def test_custom_range_missing_end_is_rejected(self):
        period = cash_flow.resolve_period("custom", start=datetime.date(2026, 1, 1), today=TODAY)
        self.assertIsNotNone(period["error"])

    def test_unknown_preset_falls_back_to_today(self):
        period = cash_flow.resolve_period("not-a-preset", today=TODAY)
        self.assertEqual(period["preset"], "today")


class CashFlowTotalsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="flow@example.com", password="StrongPass123!")
        # A declared start well before the transactions these tests create, so
        # the window arithmetic is what is under test here (the date boundary
        # itself is covered in the starting-balance tests).
        self.start = TODAY - datetime.timedelta(days=90)
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=Decimal("500000.00"),
            opening_balance_date=self.start,
        )
        self.bank = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("0.00"),
            opening_balance_date=self.start,
        )
        self.shopping = Category.objects.create(user=None, name="Shopping", kind="expense")

    def _txn(self, kind, amount, day=TODAY, fee="0.00", description="x", account=None):
        return create_transaction(
            user=self.user, account=account or self.wallet, kind=kind, category=self.shopping,
            amount=Decimal(amount), fee_amount=Decimal(fee), date=day, description=description,
        )

    def test_required_example_150k_in_185k_out(self):
        self._txn("income", "150000.00")
        self._txn("expense", "185000.00")
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["received"], Decimal("150000.00"))
        self.assertEqual(cash["spent"], Decimal("185000.00"))
        self.assertEqual(cash["difference"], Decimal("-35000.00"))
        self.assertEqual(cash["message"], "You spent 35,000.00 more than you received.")

    def test_surplus_message(self):
        self._txn("income", "200000.00")
        self._txn("expense", "150000.00")
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["difference"], Decimal("50000.00"))
        self.assertEqual(cash["message"], "You received 50,000.00 more than you spent.")

    def test_equal_amounts_message(self):
        self._txn("income", "100000.00")
        self._txn("expense", "100000.00")
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["difference"], Decimal("0.00"))
        self.assertIn("exactly equal", cash["message"])

    def test_day_with_no_activity_says_so(self):
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["received"], Decimal("0.00"))
        self.assertEqual(cash["spent"], Decimal("0.00"))
        self.assertIn("No money was recorded", cash["message"])

    def test_fee_is_included_in_money_out(self):
        self._txn("expense", "50000.00", fee="1000.00")
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["spent"], Decimal("51000.00"))

    def test_internal_transfer_does_not_inflate_either_side(self):
        create_transfer(
            user=self.user, from_account=self.wallet, to_account=self.bank,
            amount=Decimal("100000.00"), fee_amount=Decimal("0.00"), occurred_on=TODAY,
        )
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["received"], Decimal("0.00"))
        self.assertEqual(cash["spent"], Decimal("0.00"))
        self.assertEqual(cash["difference"], Decimal("0.00"))

    def test_transfer_fee_counts_as_money_out_only(self):
        create_transfer(
            user=self.user, from_account=self.wallet, to_account=self.bank,
            amount=Decimal("100000.00"), fee_amount=Decimal("500.00"), occurred_on=TODAY,
        )
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["received"], Decimal("0.00"))
        self.assertEqual(cash["spent"], Decimal("500.00"))

    def test_cash_loan_is_money_in_but_never_income(self):
        create_borrowing(
            user=self.user, lender_name="Lender", amount=Decimal("300000.00"),
            date_borrowed=TODAY, receiving_account=self.wallet,
        )
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["received"], Decimal("300000.00"))
        self.assertEqual(cash["loan_receipt_total"], Decimal("300000.00"))
        accrual = cash_flow.accrual_summary(self.user, TODAY, TODAY)
        self.assertEqual(accrual["income"], Decimal("0.00"))

    def test_debt_repayment_principal_is_out_but_not_an_expense(self):
        borrowing = create_borrowing(
            user=self.user, lender_name="Lender", amount=Decimal("300000.00"),
            date_borrowed=TODAY - datetime.timedelta(days=5), receiving_account=self.bank,
        )
        record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            fee_amount=Decimal("1500.00"), date=TODAY, paying_account=self.wallet,
        )
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["spent"], Decimal("101500.00"))
        self.assertEqual(cash["debt_repayment_total"], Decimal("100000.00"))
        self.assertEqual(cash["fee_total"], Decimal("1500.00"))
        accrual = cash_flow.accrual_summary(self.user, TODAY, TODAY)
        self.assertEqual(accrual["expenses"], Decimal("1500.00"))

    def test_goods_on_credit_is_an_expense_with_no_cash_movement(self):
        create_borrowing(
            user=self.user, lender_name="Supplier", amount=Decimal("400000.00"),
            date_borrowed=TODAY, debt_type="goods_credit",
        )
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["received"], Decimal("0.00"))
        self.assertEqual(cash["spent"], Decimal("0.00"))

    def test_multi_day_breakdown_includes_every_day_and_a_correct_total(self):
        self._txn("income", "150000.00", day=TODAY - datetime.timedelta(days=2))
        self._txn("expense", "250000.00", day=TODAY - datetime.timedelta(days=1))
        cash = cash_flow.period_cash_flow(self.user, TODAY - datetime.timedelta(days=2), TODAY)
        self.assertEqual(len(cash["rows"]), 3)
        self.assertEqual(cash["received"], Decimal("150000.00"))
        self.assertEqual(cash["spent"], Decimal("250000.00"))
        self.assertEqual(cash["difference"], Decimal("-100000.00"))
        by_date = {row["date"]: row for row in cash["rows"]}
        self.assertEqual(by_date[TODAY]["received"], Decimal("0.00"))
        self.assertFalse(by_date[TODAY]["has_activity"])
        # Rows must sum exactly to the period total.
        self.assertEqual(sum(row["received"] for row in cash["rows"]), cash["received"])
        self.assertEqual(sum(row["spent"] for row in cash["rows"]), cash["spent"])

    def test_60_day_period_has_60_rows(self):
        cash = cash_flow.period_cash_flow(self.user, TODAY - datetime.timedelta(days=59), TODAY)
        self.assertEqual(len(cash["rows"]), 60)

    def test_weekly_comparison_splits_into_blocks_that_sum_to_the_total(self):
        self._txn("income", "100000.00", day=TODAY - datetime.timedelta(days=10))
        self._txn("expense", "60000.00", day=TODAY - datetime.timedelta(days=3))
        start = TODAY - datetime.timedelta(days=13)
        cash = cash_flow.period_cash_flow(self.user, start, TODAY)
        weeks = cash_flow.weekly_comparison(cash["rows"], start, TODAY)
        self.assertEqual(len(weeks), 2)  # 14 days => two 7-day blocks
        self.assertEqual(sum(week["received"] for week in weeks), cash["received"])
        self.assertEqual(sum(week["spent"] for week in weeks), cash["spent"])
        self.assertTrue(weeks[0]["label"].startswith("Week 1"))

    def test_other_users_money_is_never_included(self):
        other = User.objects.create_user(email="other-flow@example.com", password="StrongPass123!")
        other_account = FinancialAccount.objects.create(
            user=other, name="Theirs", account_type="cash", opening_balance=Decimal("999999.00")
        )
        create_transaction(
            user=other, account=other_account, kind="expense", category=None,
            amount=Decimal("888888.00"), fee_amount=Decimal("0.00"), date=TODAY, description="theirs",
        )
        cash = cash_flow.period_cash_flow(self.user, TODAY, TODAY)
        self.assertEqual(cash["spent"], Decimal("0.00"))


class DayDetailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="detail@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=Decimal("500000.00")
        )
        self.bank = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("100000.00")
        )
        self.food = Category.objects.create(user=None, name="Food", kind="expense")

    def test_a_transaction_is_listed_once_with_its_fee_attached(self):
        create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=self.food,
            amount=Decimal("50000.00"), fee_amount=Decimal("1000.00"), date=TODAY, description="Lunch",
        )
        detail = cash_flow.day_detail(self.user, TODAY)
        self.assertEqual(len(detail["expense_transactions"]), 1)
        self.assertEqual(len(detail["fee_lines"]), 1)
        self.assertEqual(detail["fee_total"], Decimal("1000.00"))
        self.assertEqual(detail["spent"], Decimal("51000.00"))
        self.assertEqual(detail["by_category"], [("Food", Decimal("50000.00"))])
        self.assertEqual(detail["by_account"], [("Wallet", Decimal("51000.00"))])
        # The fee is not listed as a separate transaction.
        self.assertEqual(len(detail["transactions"]), 1)

    def test_repayment_and_its_fee_are_shown_once(self):
        borrowing = create_borrowing(
            user=self.user, lender_name="Creditor", amount=Decimal("200000.00"),
            date_borrowed=TODAY - datetime.timedelta(days=3), receiving_account=self.bank,
        )
        record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            fee_amount=Decimal("1500.00"), date=TODAY, paying_account=self.wallet,
        )
        detail = cash_flow.day_detail(self.user, TODAY)
        self.assertEqual(len(detail["repayments"]), 1)
        self.assertEqual(detail["repayments"][0].fee_amount, Decimal("1500.00"))
        self.assertEqual(detail["spent"], Decimal("101500.00"))
        self.assertEqual(len(detail["fee_lines"]), 1)

    def test_transfers_are_shown_but_excluded_from_totals(self):
        create_transfer(
            user=self.user, from_account=self.wallet, to_account=self.bank,
            amount=Decimal("40000.00"), fee_amount=Decimal("0.00"), occurred_on=TODAY,
            description="Moving money",
        )
        detail = cash_flow.day_detail(self.user, TODAY)
        self.assertEqual(len(detail["transfers"]), 1)
        self.assertEqual(detail["received"], Decimal("0.00"))
        self.assertEqual(detail["spent"], Decimal("0.00"))


class DashboardTodayTotalsTests(TestCase):
    """The dashboard's "Received today"/"Spent today" tiles and the Daily
    Closing page share `day_breakdown`; neither may count both legs of an
    internal transfer as real money in and out."""

    def setUp(self):
        self.user = User.objects.create_user(email="today-tiles@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=Decimal("500000.00")
        )
        self.bank = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("0.00")
        )

    def test_internal_transfer_does_not_inflate_todays_tiles(self):
        from apps.closing.services import day_breakdown

        create_transfer(
            user=self.user, from_account=self.wallet, to_account=self.bank,
            amount=Decimal("100000.00"), fee_amount=Decimal("500.00"), occurred_on=TODAY,
        )
        breakdown = day_breakdown(self.user, TODAY)
        self.assertEqual(breakdown["received"], Decimal("0.00"))
        self.assertEqual(breakdown["spent"], Decimal("500.00"))
        self.assertEqual(breakdown["transfers_in"], Decimal("100000.00"))
        self.assertEqual(breakdown["transfers_out"], Decimal("100000.00"))

    def test_real_income_and_expenses_still_count(self):
        from apps.closing.services import day_breakdown

        create_transaction(
            user=self.user, account=self.wallet, kind="income", category=None,
            amount=Decimal("250000.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Sale",
        )
        create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("40000.00"), fee_amount=Decimal("1000.00"), date=TODAY, description="Stock",
        )
        breakdown = day_breakdown(self.user, TODAY)
        self.assertEqual(breakdown["received"], Decimal("250000.00"))
        self.assertEqual(breakdown["spent"], Decimal("41000.00"))
        self.assertEqual(breakdown["net"], Decimal("209000.00"))
