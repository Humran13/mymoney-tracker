from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel, UserOwnedModel, money_field


class Category(models.Model):
    """Global default categories have user=None. A user's custom categories
    are private to them (enforced by callers filtering on Q(user=None) |
    Q(user=request.user))."""

    class Kind(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="categories"
    )
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    icon = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name", "kind"], name="unique_category_per_user"),
        ]

    def __str__(self):
        return self.name


class Transaction(UserOwnedModel):
    class Kind(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    amount = money_field()
    fee_amount = money_field(default=0)
    date = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["user", "date"]),
            models.Index(fields=["user", "kind", "date"]),
            models.Index(fields=["account", "date"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} on {self.date}"


class TransactionTemplate(UserOwnedModel):
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=Transaction.Kind.choices)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    default_amount = money_field(null=True, blank=True)
    is_favorite = models.BooleanField(default=True)
    use_count = models.PositiveIntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-use_count", "name"]

    def __str__(self):
        return self.name
