from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.currency import DEFAULT_CURRENCY_CODE
from apps.core.models import TimeStampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_suspended = models.BooleanField(
        default=False, help_text="Administrator-suspended accounts cannot log in."
    )
    is_deactivated = models.BooleanField(
        default=False,
        help_text="Self-service deactivation. Logging in again automatically reactivates the account.",
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)

    date_joined = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self):
        return self.email

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def get_short_name(self):
        return self.first_name or self.email

    @property
    def is_logged_in_eligible(self):
        return self.is_active and not self.is_suspended


class UserProfile(TimeStampedModel):
    THEME_CHOICES = [("light", "Light"), ("dark", "Dark"), ("system", "System")]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=150, blank=True)
    currency_code = models.CharField(max_length=3, default=DEFAULT_CURRENCY_CODE)
    timezone = models.CharField(max_length=64, default="Africa/Kampala")
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default="system")
    first_time_setup_complete = models.BooleanField(default=False)

    def __str__(self):
        return f"Profile<{self.user.email}>"


class UserSettings(TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="settings")
    budget_warning_threshold_1 = models.PositiveSmallIntegerField(default=80)
    budget_warning_threshold_2 = models.PositiveSmallIntegerField(default=100)
    bill_reminder_days_ahead = models.PositiveSmallIntegerField(default=3)
    notifications_enabled = models.BooleanField(default=True)
    default_account = models.ForeignKey(
        "accounts.FinancialAccount",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    def __str__(self):
        return f"Settings<{self.user.email}>"
