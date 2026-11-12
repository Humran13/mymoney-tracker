from decimal import Decimal

from django.db import models

from apps.core.models import UserOwnedModel, money_field


class Borrowing(UserOwnedModel):
    """Something the user owes someone else. Only a CASH_LOAN actually puts
    money into one of the user's accounts — goods/services received on
    credit (or any other noncash obligation) increase what's owed without
    ever touching cash, so they must never get a BORROW_RECEIVE posting."""

    class DebtType(models.TextChoices):
        CASH_LOAN = "cash_loan", "Cash Loan"
        GOODS_CREDIT = "goods_credit", "Goods on Credit"
        SERVICES_CREDIT = "services_credit", "Services on Credit"
        OTHER = "other", "Other Money Owed"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SETTLED = "settled", "Settled"

    debt_type = models.CharField(
        max_length=20,
        choices=DebtType.choices,
        default=DebtType.CASH_LOAN,
        help_text="Cash Loan is the only type that actually deposits money into an account.",
    )
    lender_name = models.CharField(max_length=150, help_text="Lender, supplier, or creditor.")
    amount = money_field()
    date_borrowed = models.DateField()
    receiving_account = models.ForeignKey(
        "accounts.FinancialAccount",
        on_delete=models.PROTECT,
        related_name="borrowings",
        null=True,
        blank=True,
        help_text="Required for a Cash Loan only — the account the money actually landed in.",
    )
    due_date = models.DateField(null=True, blank=True)
    interest_amount = money_field(default=0, help_text="Total interest owed in addition to the principal, if any.")
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["-date_borrowed", "-id"]

    def __str__(self):
        return f"Borrowed {self.amount} from {self.lender_name}"

    def total_owed(self) -> Decimal:
        return self.amount + self.interest_amount

    def total_repaid(self) -> Decimal:
        return self.repayments.aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

    def outstanding(self) -> Decimal:
        return self.total_owed() - self.total_repaid()

    def is_overdue(self) -> bool:
        from django.utils import timezone

        return bool(self.due_date and self.due_date < timezone.localdate() and self.outstanding() > 0)

    def receipt_affects_current_balance(self) -> bool:
        """True only if this loan's cash receipt is inside the receiving
        account's opening-balance window.

        A cash loan dated before the account's snapshot date is already part
        of the opening balance — the money may well have been spent long ago.
        Such a loan must still increase what's owed, but must never increase
        the account balance a second time. Accounts with no snapshot date
        (`opening_balance_date IS NULL`, i.e. every pre-Phase-11 account) keep
        the legacy include-everything behavior, which is exactly why those
        accounts need the reconciliation review in
        `apps.accounts.reconciliation`.
        """
        if self.debt_type != Borrowing.DebtType.CASH_LOAN or not self.receiving_account_id:
            return False
        cutoff = self.receiving_account.opening_balance_date
        return cutoff is None or self.date_borrowed >= cutoff


class BorrowingRepayment(UserOwnedModel):
    """A payment made against a Borrowing.

    `amount` is the principal actually applied to the debt. `fee_amount` is
    the transaction charge the payment channel skimmed off on the way — it is
    real cash that left the paying account, but it is *not* part of the debt
    being settled, so it must never reduce the outstanding balance. Adding it
    to `amount` would understate the debt; ignoring it would overstate the
    account balance. Both are posted separately (see
    `apps.debts.services._repayment_postings`).
    """

    borrowing = models.ForeignKey(Borrowing, on_delete=models.CASCADE, related_name="repayments")
    amount = money_field()
    fee_amount = money_field(
        default=0,
        help_text="Transaction charge charged by the payment channel (MTN/Airtel/bank). Reduces the account balance, never the debt.",
    )
    date = models.DateField()
    paying_account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Repaid {self.amount} towards {self.borrowing.lender_name}"

    def total_cash_out(self) -> Decimal:
        """Cash actually removed from the paying account: principal + fee."""
        return self.amount + (self.fee_amount or Decimal("0.00"))


class Lending(UserOwnedModel):
    """Money the user lent to someone else. Sending it reduces a real
    account balance but is never counted as an ordinary expense."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SETTLED = "settled", "Settled"

    borrower_name = models.CharField(max_length=150)
    amount = money_field()
    date_lent = models.DateField()
    source_account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="lendings")
    due_date = models.DateField(null=True, blank=True)
    interest_amount = money_field(default=0, help_text="Total interest expected in addition to the principal, if any.")
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["-date_lent", "-id"]

    def __str__(self):
        return f"Lent {self.amount} to {self.borrower_name}"

    def total_expected(self) -> Decimal:
        return self.amount + self.interest_amount

    def total_principal_received(self) -> Decimal:
        return self.repayments.aggregate(total=models.Sum("principal_amount"))["total"] or Decimal("0.00")

    def total_interest_received(self) -> Decimal:
        return self.repayments.aggregate(total=models.Sum("interest_amount"))["total"] or Decimal("0.00")

    def outstanding(self) -> Decimal:
        return self.amount - self.total_principal_received()

    def is_overdue(self) -> bool:
        from django.utils import timezone

        return bool(self.due_date and self.due_date < timezone.localdate() and self.outstanding() > 0)


class LendingRepayment(UserOwnedModel):
    lending = models.ForeignKey(Lending, on_delete=models.CASCADE, related_name="repayments")
    principal_amount = money_field(default=0)
    interest_amount = money_field(default=0)
    date = models.DateField()
    receiving_account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def total_received(self) -> Decimal:
        return self.principal_amount + self.interest_amount

    def __str__(self):
        return f"Received {self.total_received()} from {self.lending.borrower_name}"
