from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import FinancialAccount
from apps.closing.services import day_breakdown
from apps.core.models import AuditEvent
from apps.transactions.services import create_transaction
from apps.users.models import User


class RegistrationTests(TestCase):
    def test_registration_creates_user_profile_and_settings(self):
        response = self.client.post(
            reverse("users:register"),
            {
                "email": "newuser@example.com", "first_name": "New", "last_name": "User",
                "password1": "StrongPass123!", "password2": "StrongPass123!",
            },
        )
        user = User.objects.get(email="newuser@example.com")
        self.assertTrue(hasattr(user, "profile"))
        self.assertTrue(hasattr(user, "settings"))
        self.assertFalse(user.profile.first_time_setup_complete)
        self.assertRedirects(response, reverse("accounts:onboarding"))

    def test_duplicate_email_rejected(self):
        User.objects.create_user(email="dup@example.com", password="StrongPass123!")
        response = self.client.post(
            reverse("users:register"),
            {
                "email": "dup@example.com", "first_name": "A", "last_name": "B",
                "password1": "StrongPass123!", "password2": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 200)  # redisplays form with error
        self.assertEqual(User.objects.filter(email="dup@example.com").count(), 1)

    def test_login_audit_event_recorded(self):
        User.objects.create_user(email="loguser@example.com", password="StrongPass123!")
        self.client.post(reverse("users:login"), {"username": "loguser@example.com", "password": "StrongPass123!"})
        self.assertTrue(AuditEvent.objects.filter(action="login").exists())


class PasswordResetTests(TestCase):
    def test_password_reset_flow_sends_email_and_allows_new_password(self):
        from django.core import mail

        User.objects.create_user(email="forgot@example.com", password="OldPass123!")
        response = self.client.post(reverse("users:password_reset"), {"email": "forgot@example.com"})
        self.assertRedirects(response, reverse("users:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("reset", mail.outbox[0].subject.lower())

        import re

        match = re.search(r"/password/reset/confirm/([^/]+)/([^/\s]+)/", mail.outbox[0].body)
        uidb64, token = match.group(1), match.group(2)

        confirm_url = reverse("users:password_reset_confirm", kwargs={"uidb64": uidb64, "token": token})
        session_response = self.client.get(confirm_url, follow=True)
        set_password_url = session_response.redirect_chain[-1][0]
        response = self.client.post(set_password_url, {"new_password1": "BrandNewPass456!", "new_password2": "BrandNewPass456!"})
        self.assertRedirects(response, reverse("users:password_reset_complete"))

        self.assertTrue(self.client.login(email="forgot@example.com", password="BrandNewPass456!"))


class SelfDeactivationReactivationTests(TestCase):
    def test_deactivate_then_login_reactivates(self):
        user = User.objects.create_user(email="deact@example.com", password="StrongPass123!")
        self.client.login(email="deact@example.com", password="StrongPass123!")
        self.client.post(reverse("users:deactivate_account"), {"password": "StrongPass123!", "confirm": True})
        user.refresh_from_db()
        self.assertTrue(user.is_deactivated)

        self.client.post(reverse("users:login"), {"username": "deact@example.com", "password": "StrongPass123!"})
        user.refresh_from_db()
        self.assertFalse(user.is_deactivated)


class AdminPanelAccessTests(TestCase):
    def test_staff_user_can_access_admin_panel(self):
        staff = User.objects.create_user(email="staff@example.com", password="StrongPass123!", is_staff=True)
        self.client.login(email="staff@example.com", password="StrongPass123!")
        response = self.client.get(reverse("adminpanel:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_admin_suspend_blocks_login(self):
        target = User.objects.create_user(email="suspend@example.com", password="StrongPass123!")
        staff = User.objects.create_user(email="staff2@example.com", password="StrongPass123!", is_staff=True)
        self.client.login(email="staff2@example.com", password="StrongPass123!")
        self.client.post(reverse("adminpanel:user_suspend", args=[target.pk]))
        self.client.logout()

        login_ok = self.client.login(email="suspend@example.com", password="StrongPass123!")
        self.assertFalse(login_ok)


class DailyClosingTests(TestCase):
    def test_breakdown_matches_recorded_activity(self):
        import datetime

        user = User.objects.create_user(email="close@example.com", password="StrongPass123!")
        account = FinancialAccount.objects.create(user=user, name="Cash", account_type="cash", opening_balance=Decimal("10000.00"))
        today = datetime.date.today()
        create_transaction(user=user, account=account, kind="income", category=None,
                            amount=Decimal("20000.00"), fee_amount=Decimal("0.00"), date=today, description="")
        create_transaction(user=user, account=account, kind="expense", category=None,
                            amount=Decimal("5000.00"), fee_amount=Decimal("500.00"), date=today, description="")
        breakdown = day_breakdown(user, today)
        self.assertEqual(breakdown["received"], Decimal("20000.00"))
        self.assertEqual(breakdown["spent"], Decimal("5500.00"))
        self.assertEqual(breakdown["closing_balance"], Decimal("10000.00") + Decimal("20000.00") - Decimal("5500.00"))
