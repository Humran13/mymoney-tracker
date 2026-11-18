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
    contact = models.CharField(
        max_length=60,
        blank=True,
        help_text="Optional phone or ID. Two people with the same name stay separate if their contacts differ.",
    )
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

    def total_offset(self) -> Decimal:
        return self.offset_lines.filter(settlement__reversal__isnull=True).aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

    def outstanding(self) -> Decimal:
        return self.total_owed() - self.total_repaid() - self.total_offset()

    def is_overdue(self) -> bool:
        from django.utils import timezone

        return bool(self.due_date and self.due_date < timezone.localdate() and self.outstanding() > 0)

    def receipt_affects_current_balance(self) -> bool:
        """True only if this loan's cash receipt is inside the receiving
        account's starting window.

        A cash loan dated before the account's starting date is already part of
        the opening balance — the money may well have been spent long ago. Such
        a loan must still increase what's owed, but must never increase the
        account balance a second time. The starting date is declared or derived
        (see `apps.accounts.models.balance_start_date`), so this is correct even
        for accounts that never had an explicit `opening_balance_date`.
        """
        if self.debt_type != Borrowing.DebtType.CASH_LOAN or not self.receiving_account_id:
            return False
        return self.date_borrowed >= self.receiving_account.balance_start_date


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
    contact = models.CharField(
        max_length=60,
        blank=True,
        help_text="Optional phone or ID. Two people with the same name stay separate if their contacts differ.",
    )
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

    def total_offset(self) -> Decimal:
        return self.offset_lines.filter(settlement__reversal__isnull=True).aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

    def outstanding(self) -> Decimal:
        return self.amount - self.total_principal_received() - self.total_offset()

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


class DebtSettlement(UserOwnedModel):
    """A noncash offset between what the user owes a person and what that
    person owes the user. It reduces both sides by the same amount and never
    touches a financial account — there is deliberately no account FK and no
    AccountPosting is ever written for it. Rows are never deleted: the audit
    trail of who offset what, when, is the point."""

    person_name = models.CharField(max_length=150)
    person_key = models.CharField(max_length=250, db_index=True)
    amount = money_field()
    date = models.DateField()
    notes = models.CharField(max_length=255, blank=True)
    idempotency_key = models.UUIDField(help_text="Submitted with the confirmation form so a double-click or refresh cannot post twice.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "idempotency_key"], name="unique_settlement_token_per_user"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="settlement_amount_positive"),
        ]

    def __str__(self):
        return f"Offset {self.amount} with {self.person_name} on {self.date}"


class DebtSettlementLine(UserOwnedModel):
    """How a settlement was applied to one specific debt (borrowing) or loan
    (lending). Exactly one of the two foreign keys is set."""

    settlement = models.ForeignKey(DebtSettlement, on_delete=models.PROTECT, related_name="lines")
    borrowing = models.ForeignKey(Borrowing, on_delete=models.PROTECT, null=True, blank=True, related_name="offset_lines")
    lending = models.ForeignKey(Lending, on_delete=models.PROTECT, null=True, blank=True, related_name="offset_lines")
    amount = money_field()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(borrowing__isnull=False, lending__isnull=True) | models.Q(borrowing__isnull=True, lending__isnull=False)),
                name="settlement_line_exactly_one_target",
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="settlement_line_amount_positive"),
        ]

    def __str__(self):
        return f"{self.amount} applied to {self.borrowing or self.lending}"


class DebtSettlementReversal(UserOwnedModel):
    """Audit record that a settlement was undone. The settlement and its lines
    are never edited or deleted; `outstanding()` simply stops counting lines
    whose settlement has a reversal. The one-to-one makes a second reversal of
    the same settlement impossible at the database level. Like the settlement
    itself, it has no account FK and never produces postings."""

    settlement = models.OneToOneField(DebtSettlement, on_delete=models.PROTECT, related_name="reversal")
    date = models.DateField()
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Reversal of {self.settlement}"


class PersonIdentityLink(UserOwnedModel):
    """The user's confirmation that two records with the same name but a
    missing contact on one side are the same person. Contacts are stored
    normalized and in sorted order, so (a, b) and (b, a) are one link."""

    name_key = models.CharField(max_length=150)
    contact_a = models.CharField(max_length=60, blank=True)
    contact_b = models.CharField(max_length=60, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "name_key", "contact_a", "contact_b"], name="unique_identity_link"),
        ]

    def __str__(self):
        return f"{self.name_key}: '{self.contact_a}' = '{self.contact_b}'"
