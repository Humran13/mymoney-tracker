"""Migration 0006 only adds tables. Roll it back and forward over a database
that already holds debts, loans, repayments and an offset, and confirm none of
that history changes."""

from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.debts import services as debt_services
from apps.debts.models import Borrowing, DebtSettlement, DebtSettlementLine, Lending

from .test_debt_offsets_grouping_bill_fees import TODAY, D, lend, make_user, make_wallet, owe, settle

BEFORE = [("debts", "0005_debt_settlements_and_contact")]
LATEST = [("debts", "0007_lending_debt_type")]


class SettlementReversalMigrationTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)

    def test_history_survives_rolling_0006_back_and_forward(self):
        user = make_user("migrate@example.com")
        wallet = make_wallet(user)
        debt = owe(user, wallet, "John", "500000", notes="old debt")
        loan = lend(user, wallet, "John", "300000")
        debt_services.record_borrowing_repayment(
            user=user, borrowing=debt, amount=D("50000"), date=TODAY, paying_account=wallet, notes=""
        )
        settle(user, "John", "100000")
        snapshot = (
            [(b.pk, b.lender_name, b.contact, b.amount, b.status, b.outstanding()) for b in Borrowing.objects.all()],
            [(x.pk, x.borrower_name, x.contact, x.amount, x.status, x.outstanding()) for x in Lending.objects.all()],
            DebtSettlement.objects.count(),
            DebtSettlementLine.objects.count(),
        )
        self.assertEqual(snapshot[0][0][5], Decimal("350000.00"))

        self.migrate(BEFORE)
        self.migrate(LATEST)

        after = (
            [(b.pk, b.lender_name, b.contact, b.amount, b.status, b.outstanding()) for b in Borrowing.objects.all()],
            [(x.pk, x.borrower_name, x.contact, x.amount, x.status, x.outstanding()) for x in Lending.objects.all()],
            DebtSettlement.objects.count(),
            DebtSettlementLine.objects.count(),
        )
        self.assertEqual(snapshot, after)
        self.assertEqual(loan.outstanding(), D("200000.00"))
