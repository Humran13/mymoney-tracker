from dateutil.relativedelta import relativedelta
from django.db import models

from apps.core.models import UserOwnedModel, money_field


class RecurringBill(UserOwnedModel):
    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"

    name = models.CharField(max_length=150)
    amount = money_field()
    category = models.ForeignKey("transactions.Category", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    payment_account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.MONTHLY)
    next_due_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["next_due_date"]

    def __str__(self):
        return f"{self.name} ({self.amount} {self.frequency})"

    def is_overdue(self):
        from django.utils import timezone

        return self.status == self.Status.ACTIVE and self.next_due_date < timezone.localdate()

    def is_due_soon(self, days_ahead=3):
        from django.utils import timezone

        today = timezone.localdate()
        return self.status == self.Status.ACTIVE and today <= self.next_due_date <= today + timezone.timedelta(days=days_ahead)

    def advance_due_date(self):
        deltas = {
            self.Frequency.WEEKLY: relativedelta(weeks=1),
            self.Frequency.MONTHLY: relativedelta(months=1),
            self.Frequency.YEARLY: relativedelta(years=1),
        }
        self.next_due_date = self.next_due_date + deltas[self.frequency]


class BillPayment(UserOwnedModel):
    bill = models.ForeignKey(RecurringBill, on_delete=models.CASCADE, related_name="payments")
    due_date = models.DateField()
    paid_date = models.DateField()
    amount = money_field()
    transaction = models.OneToOneField("transactions.Transaction", on_delete=models.SET_NULL, null=True, blank=True, related_name="bill_payment")

    class Meta:
        ordering = ["-paid_date"]
        constraints = [
            models.UniqueConstraint(fields=["bill", "due_date"], name="unique_bill_payment_per_cycle"),
        ]

    def __str__(self):
        return f"{self.bill.name} paid {self.paid_date}"
