from decimal import Decimal

from django.db import models
from django.db.models import Sum

from apps.core.models import UserOwnedModel, money_field


class SavingsGoal(UserOwnedModel):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=150)
    target_amount = money_field()
    deadline = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["status", "-priority", "deadline"]

    def __str__(self):
        return self.name

    def current_saved(self) -> Decimal:
        return self.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    def remaining(self) -> Decimal:
        remaining = self.target_amount - self.current_saved()
        return remaining if remaining > 0 else Decimal("0.00")

    def progress_pct(self) -> int:
        if self.target_amount <= 0:
            return 0
        pct = (self.current_saved() / self.target_amount) * 100
        return int(min(max(pct, 0), 100))

    def is_achieved(self) -> bool:
        return self.current_saved() >= self.target_amount


class GoalAllocation(UserOwnedModel):
    """A virtual earmark against money the user already has — not a real
    transfer, and it never writes an AccountPosting, so a shilling can't be
    counted both as free cash and as allocated savings at once."""

    goal = models.ForeignKey(SavingsGoal, on_delete=models.CASCADE, related_name="allocations")
    amount = money_field(help_text="Positive to add savings, negative to record a withdrawal.")
    date = models.DateField()
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.goal.name}: {self.amount:+}"
