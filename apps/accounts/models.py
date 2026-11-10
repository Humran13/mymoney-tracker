from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimeStampedModel, UserOwnedModel, money_field


class FinancialAccount(UserOwnedModel):
    class AccountType(models.TextChoices):
        CASH = "cash", "Cash Wallet"
        CASH_SAFE = "cash_safe", "Cash Safe"
        BANK = "bank", "Bank Account"
        MOBILE_MONEY_MTN = "mobile_money_mtn", "MTN Mobile Money"
        MOBILE_MONEY_AIRTEL = "mobile_money_airtel", "Airtel Money"
        SAVINGS = "savings", "Savings Account"
        OTHER = "other", "Other Account"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=100)
    account_type = models.CharField(max_length=30, choices=AccountType.choices, default=AccountType.CASH)
    currency_code = models.CharField(max_length=3, default="UGX")
    opening_balance = money_field(default=0)
    opening_balance_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "The date the opening balance above was actually true. Postings dated on or "
            "after this date are added to the opening balance to get the current balance; "
            "postings dated before it are historical record only and do not affect the "
            "balance (they are already accounted for in the opening balance itself). "
            "Leave blank for an account with no cutoff — every posting ever recorded "
            "affects its balance (the pre-existing behavior)."
        ),
    )
    description = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_account_name_per_user"),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_account_type_display()})"


class AccountPosting(TimeStampedModel):
    """The single ledger every money movement writes to. An account's
    balance is always opening_balance + sum(postings) — never a value
    mutated in place — so it can't silently drift from recorded history,
    and editing/deleting the source object (via sync_postings/delete_postings
    in services.py) is the only way postings change, which rules out
    duplicate postings for the same source event."""

    class SourceType(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        FEE = "fee", "Transaction Fee"
        TRANSFER_OUT = "transfer_out", "Transfer Out"
        TRANSFER_IN = "transfer_in", "Transfer In"
        BORROW_RECEIVE = "borrow_receive", "Borrowed Money Received"
        BORROW_REPAY = "borrow_repay", "Loan Repayment Paid"
        LEND_SEND = "lend_send", "Money Lent"
        LEND_REPAY = "lend_repay", "Loan Repayment Received"
        BILL_PAYMENT = "bill_payment", "Bill Payment"
        ADJUSTMENT = "adjustment", "Manual Adjustment"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="postings")
    account = models.ForeignKey(FinancialAccount, on_delete=models.CASCADE, related_name="postings")
    amount = money_field()  # signed: positive increases the account, negative decreases it
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    occurred_on = models.DateField()
    description = models.CharField(max_length=255, blank=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    source = GenericForeignKey("content_type", "object_id")

    class Meta:
        ordering = ["-occurred_on", "-id"]
        indexes = [
            models.Index(fields=["account", "occurred_on"]),
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["user", "occurred_on"]),
        ]

    def __str__(self):
        return f"{self.account} {self.amount:+} ({self.source_type})"


class Transfer(UserOwnedModel):
    from_account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name="transfers_out")
    to_account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name="transfers_in")
    amount = money_field()
    fee_amount = money_field(default=0)
    occurred_on = models.DateField()
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-occurred_on", "-id"]

    def clean(self):
        if self.from_account_id and self.to_account_id and self.from_account_id == self.to_account_id:
            raise ValidationError("You can't transfer money to the same account.")

    def __str__(self):
        return f"Transfer {self.amount} {self.from_account} -> {self.to_account}"
