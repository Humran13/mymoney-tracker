from decimal import Decimal

from django.db import models

from apps.core.models import TimeStampedModel, UserOwnedModel, money_field


class Borrowing(UserOwnedModel):
    """Money the user borrowed from someone else. The principal increases a
    real account balance but is never counted as income."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SETTLED = "settled", "Settled"

    lender_name = models.CharField(max_length=150)
    amount = money_field()
    date_borrowed = models.DateField()
    receiving_account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="borrowings")
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


class BorrowingRepayment(UserOwnedModel):
    borrowing = models.ForeignKey(Borrowing, on_delete=models.CASCADE, related_name="repayments")
    amount = money_field()
    date = models.DateField()
    paying_account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Repaid {self.amount} towards {self.borrowing.lender_name}"


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
