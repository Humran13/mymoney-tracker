from apps.core.models import UserOwnedModel
from django.db import models


class DailyClosing(UserOwnedModel):
    date = models.DateField()
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    reconciliation_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="unique_closing_per_user_per_day"),
        ]

    def __str__(self):
        return f"Closing for {self.date} ({'closed' if self.is_closed else 'open'})"
