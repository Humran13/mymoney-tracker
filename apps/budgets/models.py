from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db import models
from django.db.models import Sum

from apps.core.models import UserOwnedModel, money_field


class Budget(UserOwnedModel):
    category = models.ForeignKey("transactions.Category", on_delete=models.CASCADE, related_name="budgets")
    month = models.DateField(help_text="Always stored as the 1st of the budgeted month.")
    amount = money_field()

    class Meta:
        ordering = ["-month", "category__name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "category", "month"], name="unique_budget_per_category_per_month"),
        ]

    def save(self, *args, **kwargs):
        self.month = self.month.replace(day=1)
        super().save(*args, **kwargs)

    def spent(self) -> Decimal:
        from apps.transactions.models import Transaction

        next_month = self.month + relativedelta(months=1)
        totals = Transaction.objects.filter(
            user=self.user, category=self.category, kind="expense", date__gte=self.month, date__lt=next_month
        ).aggregate(amount=Sum("amount"), fee=Sum("fee_amount"))
        return (totals["amount"] or Decimal("0.00")) + (totals["fee"] or Decimal("0.00"))

    def remaining(self) -> Decimal:
        return self.amount - self.spent()

    def percent_used(self) -> int:
        if self.amount <= 0:
            return 0
        return int(min(self.spent() / self.amount * 100, 999))

    def __str__(self):
        return f"{self.category} budget for {self.month:%B %Y}"
