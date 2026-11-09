import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import FinancialAccount
from apps.accounts.services import account_balance, create_transfer
from apps.transactions.services import create_transaction, delete_transaction, update_transaction
from apps.users.models import User


class AccountBalanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="jane@example.com", password="StrongPass123!")
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Cash Wallet", account_type="cash", opening_balance=Decimal("100000.00")
        )
        self.bank = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("0.00")
        )

    def test_opening_balance_is_not_income(self):
        self.assertEqual(account_balance(self.wallet), Decimal("100000.00"))

    def test_income_increases_balance(self):
        create_transaction(
            user=self.user, account=self.wallet, kind="income", category=None,
            amount=Decimal("50000.00"), fee_amount=Decimal("0.00"), date=datetime.date.today(), description="Salary",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("150000.00"))

    def test_mobile_money_fee_is_deducted_in_addition_to_principal(self):
        # Sending 50,000 with a 1,000 fee must deduct 51,000 total.
        create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("50000.00"), fee_amount=Decimal("1000.00"), date=datetime.date.today(), description="Send money",
        )
        self.assertEqual(account_balance(self.wallet), Decimal("49000.00"))

    def test_editing_transaction_does_not_duplicate_postings(self):
        txn = create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("10000.00"), fee_amount=Decimal("0.00"), date=datetime.date.today(), description="Lunch",
        )
        update_transaction(txn, account=self.wallet, kind="expense", category=None,
                            amount=Decimal("20000.00"), fee_amount=Decimal("0.00"),
                            date=datetime.date.today(), description="Lunch edited", notes="")
        self.assertEqual(account_balance(self.wallet), Decimal("80000.00"))
        self.assertEqual(self.wallet.postings.count(), 1)

    def test_deleting_transaction_reverses_balance(self):
        txn = create_transaction(
            user=self.user, account=self.wallet, kind="income", category=None,
            amount=Decimal("5000.00"), fee_amount=Decimal("0.00"), date=datetime.date.today(), description="Gift",
        )
        delete_transaction(txn)
        self.assertEqual(account_balance(self.wallet), Decimal("100000.00"))
        self.assertEqual(self.wallet.postings.count(), 0)

    def test_transfer_moves_money_without_changing_total_and_is_not_income_or_expense(self):
        create_transfer(
            user=self.user, from_account=self.wallet, to_account=self.bank,
            amount=Decimal("30000.00"), fee_amount=Decimal("500.00"), occurred_on=datetime.date.today(),
        )
        self.assertEqual(account_balance(self.wallet), Decimal("100000.00") - Decimal("30000.00") - Decimal("500.00"))
        self.assertEqual(account_balance(self.bank), Decimal("30000.00"))
        # Total owned money decreases only by the transaction fee, never double-counted as income/expense.
        total = account_balance(self.wallet) + account_balance(self.bank)
        self.assertEqual(total, Decimal("100000.00") - Decimal("500.00"))

    def test_decimal_precision_no_floating_point_drift(self):
        for _ in range(20):
            create_transaction(
                user=self.user, account=self.wallet, kind="expense", category=None,
                amount=Decimal("0.10"), fee_amount=Decimal("0.00"), date=datetime.date.today(), description="micro",
            )
        # 20 x 0.10 must equal exactly 2.00 — this would drift under float arithmetic.
        self.assertEqual(account_balance(self.wallet), Decimal("100000.00") - Decimal("2.00"))
