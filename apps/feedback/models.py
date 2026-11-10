import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.validators import FileExtensionValidator
from django.db import models

from apps.core.models import UserOwnedModel

# Deliberately NOT MEDIA_ROOT: that directory may be served directly by the
# webserver in front of Django (nginx/Caddy), bypassing any access check a
# Django view would make. Screenshots can contain private financial data, so
# they live somewhere nothing but feedback.views.attachment ever reads from,
# and base_url=None means accidentally rendering `.screenshot.url` in a
# template raises loudly instead of leaking a guessable public path.
private_screenshot_storage = FileSystemStorage(
    location=str(settings.BASE_DIR / "private_media" / "feedback_screenshots"),
    base_url=None,
)

MAX_SCREENSHOT_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


def _screenshot_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"{instance.user_id}/{uuid.uuid4().hex}.{ext}"


class FeedbackSubmission(UserOwnedModel):
    class Category(models.TextChoices):
        REPORT_PROBLEM = "report_problem", "Report a Problem"
        SUGGEST_IMPROVEMENT = "suggest_improvement", "Suggest an Improvement"
        ASK_FOR_HELP = "ask_for_help", "Ask for Help"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"

    category = models.CharField(max_length=30, choices=Category.choices)
    subject = models.CharField(max_length=150)
    description = models.TextField()
    screenshot = models.FileField(
        upload_to=_screenshot_upload_path,
        storage=private_screenshot_storage,
        blank=True,
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg", "webp"])],
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    admin_response = models.TextField(blank=True)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"{self.get_category_display()}: {self.subject}"
