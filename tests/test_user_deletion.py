import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import FinancialAccount
from apps.core.user_deletion import delete_user_and_all_data
from apps.debts import services as debt_services
from apps.transactions.services import create_transaction
from apps.users.models import User


class DeleteUserWithFinancialHistoryTests(TestCase):
    """Regression test: deleting a user who has ever recorded a
    transaction, transfer, borrowing, or lending used to raise
    ProtectedError, because several models reference FinancialAccount with
    on_delete=PROTECT and Django enforces that even when the protecting row
    is also being cascade-deleted in the same operation (found deploying to
    a real server with real test data)."""

    def test_user_with_full_financial_history_can_be_deleted(self):
        user = User.objects.create_user(email="todelete@example.com", password="StrongPass123!")
        account = FinancialAccount.objects.create(
            user=user, name="Wallet", account_type="cash", opening_balance=Decimal("100000.00")
        )
        other_account = FinancialAccount.objects.create(
            user=user, name="Bank", account_type="bank", opening_balance=Decimal("0.00")
        )

        create_transaction(user=user, account=account, kind="income", category=None,
                            amount=Decimal("5000.00"), fee_amount=Decimal("0.00"),
                            date=datetime.date.today(), description="")

        from apps.accounts.services import create_transfer

        create_transfer(user=user, from_account=account, to_account=other_account,
                         amount=Decimal("1000.00"), fee_amount=Decimal("0.00"),
                         occurred_on=datetime.date.today())

        borrowing = debt_services.create_borrowing(
            user=user, lender_name="John", amount=Decimal("20000.00"),
            date_borrowed=datetime.date.today(), receiving_account=account,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.record_borrowing_repayment(
            user=user, borrowing=borrowing, amount=Decimal("5000.00"),
            date=datetime.date.today(), paying_account=account, notes="",
        )

        lending = debt_services.create_lending(
            user=user, borrower_name="Sarah", amount=Decimal("8000.00"),
            date_lent=datetime.date.today(), source_account=account,
            due_date=None, interest_amount=Decimal("0.00"), notes="",
        )
        debt_services.record_lending_repayment(
            user=user, lending=lending, principal_amount=Decimal("2000.00"),
            interest_amount=Decimal("0.00"), date=datetime.date.today(),
            receiving_account=account, notes="",
        )

        # This must not raise ProtectedError.
        delete_user_and_all_data(user)

        self.assertFalse(User.objects.filter(email="todelete@example.com").exists())
        self.assertFalse(FinancialAccount.objects.filter(user_id=user.pk).exists())

    def test_deleting_a_user_never_touches_another_users_data(self):
        user_a = User.objects.create_user(email="a@example.com", password="StrongPass123!")
        user_b = User.objects.create_user(email="b@example.com", password="StrongPass123!")
        account_a = FinancialAccount.objects.create(
            user=user_a, name="A Wallet", account_type="cash", opening_balance=Decimal("1000.00")
        )
        FinancialAccount.objects.create(
            user=user_b, name="B Wallet", account_type="cash", opening_balance=Decimal("2000.00")
        )
        create_transaction(user=user_a, account=account_a, kind="income", category=None,
                            amount=Decimal("500.00"), fee_amount=Decimal("0.00"),
                            date=datetime.date.today(), description="")

        delete_user_and_all_data(user_a)

        self.assertFalse(User.objects.filter(email="a@example.com").exists())
        self.assertTrue(User.objects.filter(email="b@example.com").exists())
        self.assertTrue(FinancialAccount.objects.filter(name="B Wallet").exists())
