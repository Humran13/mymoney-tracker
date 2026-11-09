import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import FinancialAccount
from apps.accounts.services import account_balance
from apps.debts import services as debt_services
from apps.users.models import User


class BorrowingLendingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="mary@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("0.00")
        )

    def test_borrowing_increases_balance_but_is_tracked_as_debt_not_income(self):
        borrowing = debt_services.create_borrowing(
            user=self.user, lender_name="John", amount=Decimal("500000.00"),
            date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("500000.00"))
        self.assertEqual(borrowing.outstanding(), Decimal("500000.00"))

    def test_partial_repayment_reduces_outstanding_and_carries_forward(self):
        borrowing = debt_services.create_borrowing(
            user=self.user, lender_name="John", amount=Decimal("500000.00"),
            date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("200000.00"),
            date=datetime.date.today(), paying_account=self.wallet, notes="",
        )
        borrowing.refresh_from_db()
        self.assertEqual(borrowing.outstanding(), Decimal("300000.00"))
        self.assertEqual(borrowing.status, "active")  # still carries forward, not reset

    def test_full_repayment_settles_borrowing(self):
        borrowing = debt_services.create_borrowing(
            user=self.user, lender_name="John", amount=Decimal("100000.00"),
            date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("100000.00"),
            date=datetime.date.today(), paying_account=self.wallet, notes="",
        )
        borrowing.refresh_from_db()
        self.assertEqual(borrowing.outstanding(), Decimal("0.00"))
        self.assertEqual(borrowing.status, "settled")

    def test_lending_reduces_balance_and_is_not_an_expense(self):
        self.wallet.opening_balance = Decimal("1000000.00")
        self.wallet.save()
        lending = debt_services.create_lending(
            user=self.user, borrower_name="Sarah", amount=Decimal("300000.00"),
            date_lent=datetime.date.today(), source_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("700000.00"))
        self.assertEqual(lending.outstanding(), Decimal("300000.00"))

    def test_lending_partial_repayment_with_interest_recorded_separately(self):
        self.wallet.opening_balance = Decimal("1000000.00")
        self.wallet.save()
        lending = debt_services.create_lending(
            user=self.user, borrower_name="Sarah", amount=Decimal("300000.00"),
            date_lent=datetime.date.today(), source_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.record_lending_repayment(
            user=self.user, lending=lending, principal_amount=Decimal("100000.00"),
            interest_amount=Decimal("5000.00"), date=datetime.date.today(),
            receiving_account=self.wallet, notes="",
        )
        lending.refresh_from_db()
        self.assertEqual(lending.outstanding(), Decimal("200000.00"))
        # principal (100,000) + interest (5,000) both land in the account.
        self.assertEqual(account_balance(self.wallet), Decimal("700000.00") + Decimal("105000.00"))

    def test_deleting_borrowing_reverses_all_postings(self):
        borrowing = debt_services.create_borrowing(
            user=self.user, lender_name="John", amount=Decimal("100000.00"),
            date_borrowed=datetime.date.today(), receiving_account=self.wallet,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.record_borrowing_repayment(
            user=self.user, borrowing=borrowing, amount=Decimal("40000.00"),
            date=datetime.date.today(), paying_account=self.wallet, notes="",
        )
        debt_services.delete_borrowing(borrowing)
        self.assertEqual(account_balance(self.wallet), Decimal("0.00"))
