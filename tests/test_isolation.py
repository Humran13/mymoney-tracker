from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import FinancialAccount
from apps.debts.models import Borrowing
from apps.users.models import User


class WebIsolationTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(email="alice@example.com", password="StrongPass123!")
        self.user_b = User.objects.create_user(email="bob@example.com", password="StrongPass123!")
        self.account_a = FinancialAccount.objects.create(
            user=self.user_a, name="Alice Wallet", account_type="cash", opening_balance=Decimal("1000.00")
        )

    def test_user_cannot_edit_another_users_account(self):
        self.client.login(email="bob@example.com", password="StrongPass123!")
        response = self.client.post(
            reverse("accounts:update", args=[self.account_a.pk]),
            {"name": "Hacked", "account_type": "cash", "opening_balance": "0"},
        )
        self.assertEqual(response.status_code, 404)
        self.account_a.refresh_from_db()
        self.assertEqual(self.account_a.name, "Alice Wallet")

    def test_account_list_only_shows_own_accounts(self):
        FinancialAccount.objects.create(
            user=self.user_b, name="Bob Wallet", account_type="cash", opening_balance=Decimal("500.00")
        )
        self.client.login(email="bob@example.com", password="StrongPass123!")
        response = self.client.get(reverse("accounts:list"))
        self.assertContains(response, "Bob Wallet")
        self.assertNotContains(response, "Alice Wallet")

    def test_non_staff_user_cannot_access_admin_panel(self):
        self.client.login(email="bob@example.com", password="StrongPass123!")
        response = self.client.get(reverse("adminpanel:dashboard"))
        self.assertNotEqual(response.status_code, 200)


class ApiIsolationTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(email="carol@example.com", password="StrongPass123!")
        self.user_b = User.objects.create_user(email="dave@example.com", password="StrongPass123!")
        self.account_a = FinancialAccount.objects.create(
            user=self.user_a, name="Carol Wallet", account_type="cash", opening_balance=Decimal("1000.00")
        )
        self.borrowing_a = Borrowing.objects.create(
            user=self.user_a, lender_name="X", amount=Decimal("1000.00"),
            date_borrowed="2024-01-01", receiving_account=self.account_a,
        )

    def test_unauthenticated_request_is_rejected(self):
        client = APIClient()
        response = client.get("/api/v1/accounts/")
        self.assertIn(response.status_code, (401, 403))

    def test_user_cannot_list_another_users_accounts(self):
        client = APIClient()
        client.force_authenticate(self.user_b)
        response = client.get("/api/v1/accounts/")
        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.json()["results"]]
        self.assertNotIn(self.account_a.pk, ids)

    def test_user_cannot_create_repayment_against_another_users_borrowing(self):
        client = APIClient()
        client.force_authenticate(self.user_b)
        response = client.post(
            "/api/v1/borrowing-repayments/",
            {"borrowing": self.borrowing_a.pk, "amount": "100.00", "date": "2024-02-01", "paying_account": self.account_a.pk},
        )
        self.assertEqual(response.status_code, 400)  # FK not in user_b's scoped queryset

    def test_user_cannot_retrieve_another_users_account_by_id(self):
        client = APIClient()
        client.force_authenticate(self.user_b)
        response = client.get(f"/api/v1/accounts/{self.account_a.pk}/")
        self.assertEqual(response.status_code, 404)
