from django.conf import settings
from django.db import models

from .currency import DEFAULT_CURRENCY_CODE


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UserOwnedModel(TimeStampedModel):
    """Base for every model that holds a single user's private financial
    data. The FK (not a client-supplied filter) is what enforces isolation —
    all querysets in views/serializers must filter on `user=request.user`."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(class)ss",
    )

    class Meta:
        abstract = True


class MoneyModel(models.Model):
    """Abstract base for anything that stores a monetary amount. Always a
    Decimal/NUMERIC column — never float — per the no-floating-point-money
    requirement."""

    currency_code = models.CharField(max_length=3, default=DEFAULT_CURRENCY_CODE)

    class Meta:
        abstract = True


def money_field(**kwargs):
    defaults = {"max_digits": 18, "decimal_places": 2}
    defaults.update(kwargs)
    return models.DecimalField(**defaults)


class AuditEvent(TimeStampedModel):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        ADMIN_ACTION = "admin_action", "Admin Action"
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        LOGIN_FAILED = "login_failed", "Login Failed"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    affected_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events_received",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["affected_user", "-created_at"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.action} by user#{self.actor_id} at {self.created_at:%Y-%m-%d %H:%M}"
