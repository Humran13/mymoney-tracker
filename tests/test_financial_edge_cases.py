import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import FinancialAccount
from apps.bills.models import BillPayment, RecurringBill
from apps.budgets.models import Budget
from apps.debts import services as debt_services
from apps.debts.forms import BorrowingForm, BorrowingRepaymentForm, LendingForm
from apps.debts.models import Borrowing
from apps.transactions.forms import TransactionForm
from apps.transactions.models import Category
from apps.transactions.services import create_transaction
from apps.users.models import User


class ZeroAndNegativeAmountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="edge1@example.com", password="StrongPass123!")
        self.account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("100000.00")
        )

    def _transaction_form_data(self, amount):
        return {
            "kind": "expense", "account": self.account.pk, "category": "",
            "amount": amount, "fee_amount": "0", "date": datetime.date.today(),
            "description": "test", "notes": "",
        }

    def test_zero_amount_transaction_rejected(self):
        form = TransactionForm(self._transaction_form_data("0"), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_negative_amount_transaction_rejected(self):
        form = TransactionForm(self._transaction_form_data("-5000"), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_negative_fee_rejected(self):
        data = self._transaction_form_data("5000")
        data["fee_amount"] = "-100"
        form = TransactionForm(data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("fee_amount", form.errors)

    def test_zero_amount_borrowing_rejected(self):
        form = BorrowingForm(
            {
                "lender_name": "John", "amount": "0", "date_borrowed": datetime.date.today(),
                "receiving_account": self.account.pk, "due_date": "", "interest_amount": "0", "notes": "",
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_zero_amount_lending_rejected(self):
        form = LendingForm(
            {
                "borrower_name": "Sarah", "amount": "0", "date_lent": datetime.date.today(),
                "source_account": self.account.pk, "due_date": "", "interest_amount": "0", "notes": "",
            },
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)


class OverpaymentGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="edge2@example.com", password="StrongPass123!")
        self.account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("1000000.00")
        )

    def test_borrowing_repayment_cannot_exceed_outstanding_via_form(self):
        borrowing = debt_services.create_borrowing(
            user=self.user, lender_name="John", amount=Decimal("100000.00"),
            date_borrowed=datetime.date.today(), receiving_account=self.account,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        form = BorrowingRepaymentForm(
            {"amount": "150000", "date": datetime.date.today(), "paying_account": self.account.pk, "notes": ""},
            user=self.user, borrowing=borrowing,
        )
        self.assertFalse(form.is_valid())

    def test_borrowing_repayment_cannot_exceed_outstanding_via_service(self):
        borrowing = debt_services.create_borrowing(
            user=self.user, lender_name="John", amount=Decimal("100000.00"),
            date_borrowed=datetime.date.today(), receiving_account=self.account,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        # The service layer must guard this too, since the API bypasses Django forms entirely.
        with self.assertRaises(ValueError):
            debt_services.record_borrowing_repayment(
                user=self.user, borrowing=borrowing, amount=Decimal("150000.00"),
                date=datetime.date.today(), paying_account=self.account, notes="",
            )

    def test_lending_principal_repayment_cannot_exceed_outstanding(self):
        lending = debt_services.create_lending(
            user=self.user, borrower_name="Sarah", amount=Decimal("100000.00"),
            date_lent=datetime.date.today(), source_account=self.account,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        with self.assertRaises(ValueError):
            debt_services.record_lending_repayment(
                user=self.user, lending=lending, principal_amount=Decimal("150000.00"),
                interest_amount=Decimal("0.00"), date=datetime.date.today(),
                receiving_account=self.account, notes="",
            )

    def test_exact_full_repayment_is_allowed_and_settles(self):
        borrowing = debt_services.create_borrowing(
            user=self.user, lender_name="John", amount=Decimal("100000.00"),
            date_borrowed=datetime.date.today(), receiving_account=self.account,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            date=datetime.date.today(), paying_account=self.account, notes="",
        )
        borrowing.refresh_from_db()
        self.assertEqual(borrowing.status, Borrowing.Status.SETTLED)


class DuplicatePostingRaceConditionTests(TestCase):
    def test_duplicate_bill_payment_blocked_at_the_database_level_under_a_race(self):
        """The application-level pre-check in pay_bill() is only the first
        line of defense; the real guarantee against a race (two near-
        simultaneous submissions) is the DB unique constraint itself."""
        user = User.objects.create_user(email="edge3@example.com", password="StrongPass123!")
        account = FinancialAccount.objects.create(user=user, name="Cash", account_type="cash", opening_balance=Decimal("0"))
        category = Category.objects.create(user=None, name="Rent", kind="expense")
        bill = RecurringBill.objects.create(
            user=user, name="Rent", amount=Decimal("1000.00"), category=category,
            payment_account=account, frequency="monthly", next_due_date=datetime.date(2026, 1, 1),
        )
        BillPayment.objects.create(user=user, bill=bill, due_date=datetime.date(2026, 1, 1), paid_date=datetime.date(2026, 1, 1), amount=Decimal("1000.00"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillPayment.objects.create(user=user, bill=bill, due_date=datetime.date(2026, 1, 1), paid_date=datetime.date(2026, 1, 1), amount=Decimal("1000.00"))


class MonthBoundaryAndBudgetTests(TestCase):
    def test_budget_only_counts_transactions_within_its_own_month(self):
        user = User.objects.create_user(email="edge4@example.com", password="StrongPass123!")
        account = FinancialAccount.objects.create(user=user, name="Cash", account_type="cash", opening_balance=Decimal("0"))
        category = Category.objects.create(user=None, name="Transport", kind="expense")
        budget = Budget.objects.create(user=user, category=category, month=datetime.date(2026, 1, 1), amount=Decimal("100000.00"))

        create_transaction(user=user, account=account, kind="expense", category=category,
                            amount=Decimal("40000.00"), fee_amount=Decimal("0"), date=datetime.date(2026, 1, 31), description="Jan")
        create_transaction(user=user, account=account, kind="expense", category=category,
                            amount=Decimal("99999.00"), fee_amount=Decimal("0"), date=datetime.date(2026, 2, 1), description="Feb")

        self.assertEqual(budget.spent(), Decimal("40000.00"))  # February's spending must not leak in


class ReopenAndBackfillTests(TestCase):
    def test_backfilling_a_transaction_on_a_closed_day_recalculates_balance(self):
        from apps.closing.models import DailyClosing
        from apps.closing.services import balance_as_of

        user = User.objects.create_user(email="edge5@example.com", password="StrongPass123!")
        account = FinancialAccount.objects.create(
            user=user, name="Cash", account_type="cash", opening_balance=Decimal("100000.00"),
            opening_balance_date=datetime.date(2026, 1, 1),
        )
        closed_day = datetime.date(2026, 1, 10)
        DailyClosing.objects.create(user=user, date=closed_day, is_closed=True)

        before = balance_as_of(user, closed_day)
        # A forgotten transaction, backfilled onto an already-closed day.
        create_transaction(user=user, account=account, kind="expense", category=None,
                            amount=Decimal("15000.00"), fee_amount=Decimal("0"), date=closed_day, description="forgotten")
        after = balance_as_of(user, closed_day)
        self.assertEqual(after, before - Decimal("15000.00"))

        # And every later day's balance reflects it too, automatically.
        later = balance_as_of(user, closed_day + datetime.timedelta(days=5))
        self.assertEqual(later, Decimal("100000.00") - Decimal("15000.00"))
