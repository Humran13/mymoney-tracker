import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import FinancialAccount
from apps.dashboard.services import spending_trend
from apps.transactions.models import Category
from apps.transactions.services import create_transaction
from apps.users.models import User


class SpendingTrendServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="trend@example.com", password="StrongPass123!")
        self.account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("1000000.00")
        )
        self.category = Category.objects.create(user=None, name="Food", kind="expense")

    def _spend(self, on_date, amount):
        create_transaction(
            user=self.user, account=self.account, kind="expense", category=self.category,
            amount=Decimal(amount), fee_amount=Decimal("0"), date=on_date, description="t",
        )

    def test_default_period_is_6_months_monthly_granularity(self):
        trend = spending_trend(self.user)
        self.assertEqual(trend["period"], "6m")
        self.assertEqual(len(trend["labels"]), 6)

    def test_7_days_includes_zero_spending_days(self):
        today = datetime.date.today()
        self._spend(today, "1000.00")
        trend = spending_trend(self.user, "7d")
        self.assertEqual(len(trend["labels"]), 7)
        self.assertEqual(sum(trend["totals"]), 1000.0)
        # all but one day must be zero, and none are skipped
        self.assertEqual(trend["totals"].count(0.0), 6)

    def test_30_days_daily_granularity_and_correct_total(self):
        today = datetime.date.today()
        self._spend(today, "500.00")
        self._spend(today - datetime.timedelta(days=10), "1500.00")
        trend = spending_trend(self.user, "30d")
        self.assertEqual(len(trend["labels"]), 30)
        self.assertEqual(sum(trend["totals"]), 2000.0)

    def test_custom_range_daily_grouping_for_short_span(self):
        start = datetime.date(2026, 1, 1)
        end = datetime.date(2026, 1, 5)
        self._spend(datetime.date(2026, 1, 3), "3000.00")
        trend = spending_trend(self.user, "custom", start, end)
        self.assertEqual(trend["period"], "custom")
        self.assertEqual(len(trend["labels"]), 5)
        self.assertEqual(sum(trend["totals"]), 3000.0)

    def test_custom_range_monthly_grouping_for_long_span(self):
        start = datetime.date(2025, 1, 1)
        end = datetime.date(2025, 12, 31)
        trend = spending_trend(self.user, "custom", start, end)
        self.assertEqual(len(trend["labels"]), 12)

    def test_custom_range_invalid_falls_back_to_default(self):
        trend = spending_trend(self.user, "custom", datetime.date(2026, 5, 1), datetime.date(2026, 1, 1))
        self.assertEqual(trend["period"], "6m")

    def test_unknown_period_falls_back_to_default(self):
        trend = spending_trend(self.user, "bogus")
        self.assertEqual(trend["period"], "6m")

    def test_last_year_covers_full_previous_calendar_year(self):
        today = datetime.date.today()
        trend = spending_trend(self.user, "last_year")
        self.assertTrue(trend["start"].startswith(str(today.year - 1)))
        self.assertTrue(trend["end"].startswith(str(today.year - 1)))
        self.assertEqual(len(trend["labels"]), 12)

    def test_all_time_with_no_transactions_does_not_crash(self):
        trend = spending_trend(self.user, "all_time")
        self.assertGreaterEqual(len(trend["labels"]), 1)

    def test_other_users_transactions_never_included(self):
        other = User.objects.create_user(email="other@example.com", password="StrongPass123!")
        other_account = FinancialAccount.objects.create(
            user=other, name="Cash", account_type="cash", opening_balance=Decimal("0")
        )
        create_transaction(
            user=other, account=other_account, kind="expense", category=None,
            amount=Decimal("999999.00"), fee_amount=Decimal("0"), date=datetime.date.today(), description="t",
        )
        trend = spending_trend(self.user, "7d")
        self.assertEqual(sum(trend["totals"]), 0.0)


class SpendingTrendViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="view@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("500000.00")
        )
        self.client.login(email="view@example.com", password="StrongPass123!")

    def test_trend_endpoint_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard:trend_data"))
        self.assertEqual(response.status_code, 302)

    def test_trend_endpoint_returns_json(self):
        response = self.client.get(reverse("dashboard:trend_data"), {"period": "12m"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["period"], "12m")
        self.assertEqual(len(data["labels"]), 12)

    def test_trend_endpoint_persists_period_choice(self):
        self.client.get(reverse("dashboard:trend_data"), {"period": "30d"})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.dashboard_trend_period, "30d")

    def test_trend_endpoint_persists_custom_range(self):
        self.client.get(reverse("dashboard:trend_data"), {"period": "custom", "start": "2026-01-01", "end": "2026-01-10"})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.dashboard_trend_period, "custom")
        self.assertEqual(self.user.profile.dashboard_trend_start, datetime.date(2026, 1, 1))
        self.assertEqual(self.user.profile.dashboard_trend_end, datetime.date(2026, 1, 10))

    def test_dashboard_remembers_saved_period_on_reload(self):
        self.client.get(reverse("dashboard:trend_data"), {"period": "3m"})
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, 'value="3m" selected')

    def test_trend_endpoint_is_scoped_to_logged_in_user_only(self):
        other = User.objects.create_user(email="other2@example.com", password="StrongPass123!")
        other_account = FinancialAccount.objects.create(
            user=other, name="Cash", account_type="cash", opening_balance=Decimal("0")
        )
        create_transaction(
            user=other, account=other_account, kind="expense", category=None,
            amount=Decimal("777777.00"), fee_amount=Decimal("0"), date=datetime.date.today(), description="t",
        )
        response = self.client.get(reverse("dashboard:trend_data"), {"period": "7d"})
        data = response.json()
        self.assertEqual(sum(data["totals"]), 0.0)
