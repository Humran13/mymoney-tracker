"""Phase 13 page-level tests: the analysis pages, the monthly dashboard
comparison and the opening-balance reconciliation screen."""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import FinancialAccount
from apps.debts.services import create_borrowing
from apps.transactions.services import create_transaction
from apps.users.models import User

TODAY = timezone.localdate()


class AnalysisPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="analysis@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=Decimal("500000.00")
        )
        create_transaction(
            user=self.user, account=self.wallet, kind="income", category=None,
            amount=Decimal("150000.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Sale",
        )
        create_transaction(
            user=self.user, account=self.wallet, kind="expense", category=None,
            amount=Decimal("185000.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Stock",
        )
        self.client.login(email="analysis@example.com", password="StrongPass123!")

    def test_page_defaults_to_today_and_shows_both_sides(self):
        response = self.client.get(reverse("reports:income_vs_expenses"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Income vs Expenses Analysis")
        self.assertContains(response, "UGX 150,000")
        self.assertContains(response, "UGX 185,000")
        self.assertContains(response, "You spent 35,000.00 more than you received.")
        self.assertEqual(response.context["period"]["preset"], "today")

    def test_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("reports:income_vs_expenses"))
        self.assertEqual(response.status_code, 302)

    def test_last_7_days_shows_a_daily_table_with_every_day(self):
        response = self.client.get(reverse("reports:income_vs_expenses"), {"preset": "7d"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Daily breakdown")
        self.assertEqual(len(response.context["cash"]["rows"]), 7)
        # A single 7-day block isn't a comparison, so no weekly table.
        self.assertFalse(response.context["show_weekly"])

    def test_30_days_shows_the_weekly_comparison(self):
        response = self.client.get(reverse("reports:income_vs_expenses"), {"preset": "30d"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Weekly comparison")
        self.assertTrue(response.context["show_weekly"])
        self.assertEqual(len(response.context["cash"]["weekly"]), 5)  # 30 days => 5 blocks

    def test_60_days_is_supported(self):
        response = self.client.get(reverse("reports:income_vs_expenses"), {"preset": "60d"})
        self.assertEqual(len(response.context["cash"]["rows"]), 60)

    def test_custom_range_is_honoured(self):
        response = self.client.get(
            reverse("reports:income_vs_expenses"),
            {"preset": "custom", "start": "2026-01-01", "end": "2026-01-15"},
        )
        self.assertEqual(response.status_code, 200)
        period = response.context["period"]
        self.assertEqual(period["start"], datetime.date(2026, 1, 1))
        self.assertEqual(period["end"], datetime.date(2026, 1, 15))

    def test_invalid_custom_range_shows_a_message_not_a_crash(self):
        response = self.client.get(
            reverse("reports:income_vs_expenses"),
            {"preset": "custom", "start": "2026-05-01", "end": "2026-01-01"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["period"]["error"])

    def test_drilldown_page_lists_the_day_and_links_back(self):
        response = self.client.get(reverse("reports:income_vs_expenses_day", args=[TODAY.isoformat()]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sale")
        self.assertContains(response, "Stock")
        self.assertContains(response, reverse("reports:income_vs_expenses"))

    def test_drilldown_rejects_an_unreadable_date(self):
        response = self.client.get(reverse("reports:income_vs_expenses_day", args=["not-a-date"]))
        self.assertEqual(response.status_code, 404)

    def test_drilldown_is_isolated_per_user(self):
        other = User.objects.create_user(email="analysis-other@example.com", password="StrongPass123!")
        other_wallet = FinancialAccount.objects.create(
            user=other, name="Theirs", account_type="cash", opening_balance=Decimal("100.00")
        )
        create_transaction(
            user=other, account=other_wallet, kind="expense", category=None,
            amount=Decimal("777777.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Secret purchase",
        )
        response = self.client.get(reverse("reports:income_vs_expenses_day", args=[TODAY.isoformat()]))
        self.assertNotContains(response, "Secret purchase")
        self.assertNotContains(response, "777,777")


class MonthlyDashboardComparisonTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="monthly@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.wallet = FinancialAccount.objects.create(
            user=self.user, name="Wallet", account_type="cash", opening_balance=Decimal("100000.00")
        )
        self.client.login(email="monthly@example.com", password="StrongPass123!")

    def _txn(self, kind, amount, day=None):
        create_transaction(
            user=self.user, account=self.wallet, kind=kind, category=None,
            amount=Decimal(amount), fee_amount=Decimal("0.00"), date=day or TODAY, description=kind,
        )

    def test_card_shows_current_month_income_expenses_and_difference(self):
        self._txn("income", "2500000.00")
        self._txn("expense", "2900000.00")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["monthly_income"], Decimal("2500000.00"))
        self.assertEqual(response.context["monthly_expenses"], Decimal("2900000.00"))
        self.assertEqual(response.context["monthly_difference"], Decimal("-400000.00"))
        self.assertContains(response, "UGX 2,500,000")
        self.assertContains(response, "UGX 2,900,000")
        self.assertContains(response, "UGX -400,000")

    def test_warning_names_the_shortfall_and_links_to_the_analysis_page(self):
        self._txn("income", "2500000.00")
        self._txn("expense", "2900000.00")
        response = self.client.get(reverse("dashboard:home"))
        warnings = response.context["warnings"]
        shortfall_warning = warnings[0]
        self.assertIn("exceed your income", shortfall_warning["text"])
        self.assertIn("UGX 400,000", shortfall_warning["text"])
        self.assertEqual(shortfall_warning["url"], reverse("reports:income_vs_expenses"))
        self.assertContains(response, reverse("reports:income_vs_expenses"))

    def test_no_shortfall_warning_when_income_exceeds_expenses(self):
        self._txn("income", "500000.00")
        self._txn("expense", "100000.00")
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.context["monthly_difference"], Decimal("400000.00"))
        self.assertFalse(any("exceed your income" in w["text"] for w in response.context["warnings"]))

    def test_another_calendar_month_can_be_selected(self):
        previous_month_day = TODAY.replace(day=1) - datetime.timedelta(days=1)
        self._txn("income", "700000.00", day=previous_month_day)
        self._txn("expense", "900000.00", day=previous_month_day)
        value = previous_month_day.strftime("%Y-%m")
        response = self.client.get(reverse("dashboard:home"), {"month": value})
        self.assertEqual(response.context["month_value"], value)
        self.assertEqual(response.context["monthly_income"], Decimal("700000.00"))
        self.assertEqual(response.context["monthly_expenses"], Decimal("900000.00"))
        self.assertEqual(response.context["monthly_difference"], Decimal("-200000.00"))
        self.assertFalse(response.context["month_is_current"])

    def test_invalid_month_falls_back_to_the_current_month(self):
        response = self.client.get(reverse("dashboard:home"), {"month": "garbage"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["month_value"], TODAY.strftime("%Y-%m"))
        self.assertTrue(response.context["month_is_current"])

    def test_existing_dashboard_sections_are_untouched(self):
        self._txn("income", "100000.00")
        response = self.client.get(reverse("dashboard:home"))
        for key in (
            "total_balance", "today_received", "today_spent", "today_net",
            "money_owed_by_me", "money_owed_to_me", "upcoming_bills", "goals",
            "trend_labels", "trend_totals",
        ):
            self.assertIn(key, response.context, key)


class ReconciliationPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="recon@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.legacy = FinancialAccount.objects.create(
            user=self.user, name="WALLET", account_type="cash", opening_balance=Decimal("500000.00")
        )
        create_borrowing(
            user=self.user, lender_name="Old Loan", amount=Decimal("1292000.00"),
            date_borrowed=TODAY - datetime.timedelta(days=90), receiving_account=self.legacy,
        )
        self.client.login(email="recon@example.com", password="StrongPass123!")

    def test_page_renders_the_read_only_preview(self):
        response = self.client.get(reverse("accounts:reconciliation"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Opening balance reconciliation")
        self.assertContains(response, "Old Loan")
        self.assertEqual(response.context["needs_review_count"], 1)

    def test_preview_post_shows_the_projection_without_saving(self):
        response = self.client.post(
            reverse("accounts:opening_snapshot", args=[self.legacy.pk]),
            {"opening_balance_date": "2026-11-10"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview — not applied yet")
        self.legacy.refresh_from_db()
        self.assertIsNone(self.legacy.opening_balance_date)

    def test_confirmed_post_applies_only_the_snapshot_date(self):
        response = self.client.post(
            reverse("accounts:opening_snapshot", args=[self.legacy.pk]),
            {"opening_balance_date": "2026-11-10", "confirm": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.opening_balance_date, datetime.date(2026, 11, 10))
        self.assertEqual(self.legacy.opening_balance, Decimal("500000.00"))
        self.assertEqual(self.legacy.postings.count(), 1)

    def test_missing_date_is_rejected(self):
        response = self.client.post(
            reverse("accounts:opening_snapshot", args=[self.legacy.pk]),
            {"opening_balance_date": "", "confirm": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.legacy.refresh_from_db()
        self.assertIsNone(self.legacy.opening_balance_date)

    def test_another_user_cannot_touch_this_account(self):
        User.objects.create_user(email="recon-other@example.com", password="StrongPass123!")
        self.client.logout()
        self.client.login(email="recon-other@example.com", password="StrongPass123!")
        response = self.client.post(
            reverse("accounts:opening_snapshot", args=[self.legacy.pk]),
            {"opening_balance_date": "2026-11-10", "confirm": "1"},
        )
        self.assertEqual(response.status_code, 404)
        self.legacy.refresh_from_db()
        self.assertIsNone(self.legacy.opening_balance_date)
