from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.users.models import User


class CsrfProtectionTests(TestCase):
    def test_post_without_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        User.objects.create_user(email="csrf@example.com", password="StrongPass123!")
        response = client.post(
            reverse("users:login"), {"username": "csrf@example.com", "password": "StrongPass123!"}
        )
        self.assertEqual(response.status_code, 403)

    def test_post_with_csrf_token_succeeds(self):
        client = Client(enforce_csrf_checks=True)
        User.objects.create_user(email="csrf2@example.com", password="StrongPass123!")
        client.get(reverse("users:login"))  # primes the csrf cookie
        csrf_token = client.cookies["csrftoken"].value
        response = client.post(
            reverse("users:login"),
            {"username": "csrf2@example.com", "password": "StrongPass123!", "csrfmiddlewaretoken": csrf_token},
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 302)


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3, AXES_COOLOFF_TIME=1)
class LoginRateLimitingTests(TestCase):
    def test_repeated_failed_logins_are_locked_out(self):
        User.objects.create_user(email="lockout@example.com", password="StrongPass123!")
        login_url = reverse("users:login")

        # AXES_FAILURE_LIMIT=3: the first 2 failures just redisplay the form.
        for _ in range(2):
            response = self.client.post(login_url, {"username": "lockout@example.com", "password": "WrongPassword!"})
            self.assertEqual(response.status_code, 200)

        # The 3rd failure trips the lockout itself (axes blocks on reaching the limit).
        response = self.client.post(login_url, {"username": "lockout@example.com", "password": "WrongPassword!"})
        self.assertEqual(response.status_code, 429)

        # And now even the CORRECT password is blocked while locked out.
        response = self.client.post(login_url, {"username": "lockout@example.com", "password": "StrongPass123!"})
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)


class JwtValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="jwt@example.com", password="StrongPass123!")

    def test_request_without_token_is_unauthorized(self):
        client = APIClient()
        response = client.get("/api/v1/accounts/")
        self.assertIn(response.status_code, (401, 403))

    def test_garbage_token_is_rejected(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        response = client.get("/api/v1/accounts/")
        # DRF returns 401 or 403 here depending on exactly which configured
        # authenticator ends up reporting the failure; both correctly deny
        # access, which is what actually matters.
        self.assertIn(response.status_code, (401, 403))

    def test_valid_token_obtained_via_token_endpoint_is_accepted(self):
        client = APIClient()
        token_response = client.post(
            "/api/v1/auth/token/", {"email": self.user.email, "password": "StrongPass123!"}
        )
        self.assertEqual(token_response.status_code, 200)
        access = token_response.json()["access"]
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = client.get("/api/v1/accounts/")
        self.assertEqual(response.status_code, 200)


class NoRawSqlOrUnsafeTemplateOutputTests(TestCase):
    def test_login_page_escapes_a_malicious_email_value(self):
        """A value echoed back into a form (e.g. on validation failure)
        must never be rendered unescaped."""
        response = self.client.post(
            reverse("users:login"),
            {"username": '<script>alert(1)</script>@example.com', "password": "x"},
        )
        self.assertNotContains(response, "<script>alert(1)</script>", status_code=200)
