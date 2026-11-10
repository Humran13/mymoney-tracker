"""Phase 11 Priority 5: Help & Feedback.

Covers: category/subject/description/screenshot submission, status
lifecycle, admin response, strict isolation between users (a user must
never see or reach another user's submission, by URL or by listing), and
secure attachment serving (owner + staff only, never a raw public path --
see apps/feedback/models.py for why screenshots live outside MEDIA_ROOT).
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.feedback.forms import FeedbackSubmissionForm
from apps.feedback.models import FeedbackSubmission
from apps.users.models import User


def _tiny_png(name="shot.png"):
    # Smallest possible valid PNG file signature + minimal data -- enough
    # for FileExtensionValidator and storage, not a real image.
    content = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return SimpleUploadedFile(name, content, content_type="image/png")


class FeedbackFormTests(TestCase):
    def test_rejects_disallowed_file_extension(self):
        bad_file = SimpleUploadedFile("shot.exe", b"not an image", content_type="application/octet-stream")
        form = FeedbackSubmissionForm(
            data={"category": "report_problem", "subject": "x", "description": "y"},
            files={"screenshot": bad_file},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("screenshot", form.errors)

    def test_rejects_oversized_screenshot(self):
        huge = SimpleUploadedFile("shot.png", b"0" * (5 * 1024 * 1024 + 1), content_type="image/png")
        form = FeedbackSubmissionForm(
            data={"category": "report_problem", "subject": "x", "description": "y"},
            files={"screenshot": huge},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("screenshot", form.errors)

    def test_accepts_valid_submission_without_screenshot(self):
        form = FeedbackSubmissionForm(
            data={"category": "suggest_improvement", "subject": "Add dark mode toggle to reports", "description": "Please"}
        )
        self.assertTrue(form.is_valid())


class FeedbackViewTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(email="alice@example.com", password="StrongPass123!")
        self.bob = User.objects.create_user(email="bob@example.com", password="StrongPass123!")
        self.staff = User.objects.create_user(email="staff@example.com", password="StrongPass123!", is_staff=True)
        self.submission = FeedbackSubmission.objects.create(
            user=self.alice, category="ask_for_help", subject="How do I record a loan?",
            description="I can't find the button.", screenshot=_tiny_png(),
        )

    def test_create_requires_login(self):
        response = self.client.get(reverse("feedback:create"))
        self.assertEqual(response.status_code, 302)

    def test_user_can_create_and_view_own_submission(self):
        self.client.login(email="bob@example.com", password="StrongPass123!")
        response = self.client.post(
            reverse("feedback:create"),
            {"category": "report_problem", "subject": "Dashboard chart is blank", "description": "Shows nothing on load."},
        )
        self.assertEqual(response.status_code, 302)
        bob_submission = FeedbackSubmission.objects.get(user=self.bob)
        self.assertEqual(bob_submission.status, FeedbackSubmission.Status.PENDING)
        detail = self.client.get(reverse("feedback:detail", args=[bob_submission.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Dashboard chart is blank", detail.content)

    def test_user_cannot_view_another_users_submission(self):
        self.client.login(email="bob@example.com", password="StrongPass123!")
        response = self.client.get(reverse("feedback:detail", args=[self.submission.pk]))
        self.assertEqual(response.status_code, 404)

    def test_user_list_only_shows_own_submissions(self):
        FeedbackSubmission.objects.create(
            user=self.bob, category="report_problem", subject="Bob reports an issue", description="x",
        )
        self.client.login(email="bob@example.com", password="StrongPass123!")
        response = self.client.get(reverse("feedback:list"))
        self.assertContains(response, "Bob reports an issue")
        self.assertNotContains(response, "How do I record a loan?")

    def test_attachment_served_to_owner(self):
        self.client.login(email="alice@example.com", password="StrongPass123!")
        response = self.client.get(reverse("feedback:attachment", args=[self.submission.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_attachment_served_to_staff(self):
        self.client.login(email="staff@example.com", password="StrongPass123!")
        response = self.client.get(reverse("feedback:attachment", args=[self.submission.pk]))
        self.assertEqual(response.status_code, 200)

    def test_attachment_not_served_to_another_regular_user(self):
        self.client.login(email="bob@example.com", password="StrongPass123!")
        response = self.client.get(reverse("feedback:attachment", args=[self.submission.pk]))
        self.assertEqual(response.status_code, 404)

    def test_attachment_not_served_to_anonymous_user(self):
        response = self.client.get(reverse("feedback:attachment", args=[self.submission.pk]))
        self.assertEqual(response.status_code, 302)  # redirected to login


class AdminFeedbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="carol@example.com", password="StrongPass123!")
        self.staff = User.objects.create_user(email="staff2@example.com", password="StrongPass123!", is_staff=True)
        self.submission = FeedbackSubmission.objects.create(
            user=self.user, category="suggest_improvement", subject="Export to Excel", description="Would help a lot.",
        )

    def test_non_staff_cannot_access_admin_feedback_list(self):
        self.client.login(email="carol@example.com", password="StrongPass123!")
        response = self.client.get(reverse("adminpanel:feedback_list"))
        self.assertEqual(response.status_code, 302)

    def test_staff_can_filter_and_respond(self):
        self.client.login(email="staff2@example.com", password="StrongPass123!")
        list_response = self.client.get(reverse("adminpanel:feedback_list"), {"category": "suggest_improvement"})
        self.assertContains(list_response, "Export to Excel")

        response = self.client.post(
            reverse("adminpanel:feedback_detail", args=[self.submission.pk]),
            {"status": "resolved", "admin_response": "Added in the latest release!"},
        )
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, FeedbackSubmission.Status.RESOLVED)
        self.assertEqual(self.submission.admin_response, "Added in the latest release!")
        self.assertEqual(self.submission.responded_by, self.staff)
        self.assertIsNotNone(self.submission.responded_at)

    def test_user_sees_admin_response_on_their_own_submission(self):
        self.submission.status = FeedbackSubmission.Status.RESOLVED
        self.submission.admin_response = "Fixed!"
        self.submission.save()
        self.client.login(email="carol@example.com", password="StrongPass123!")
        response = self.client.get(reverse("feedback:detail", args=[self.submission.pk]))
        self.assertContains(response, "Fixed!")
