"""Phase 13, improvements 2, 3 and 4.

* 2 — "Last 24 Hours" in the spending-trend selector, honestly implemented as
  today's calendar-day total (transactions store a date, not a timestamp).
* 3/4 — the Charity and Shipping Fees expense categories.
"""

import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import FinancialAccount
from apps.dashboard.services import TREND_24H_NOTE, TREND_PERIOD_CHOICES, spending_trend
from apps.reports.services import compute_monthly_report
from apps.transactions.forms import TransactionForm
from apps.transactions.models import Category
from apps.transactions.services import create_transaction
from apps.users.models import User

TODAY = timezone.localdate()


class Last24HoursTrendTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="trend24@example.com", password="StrongPass123!")
        self.account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("1000000.00")
        )

    def _spend(self, day, amount):
        create_transaction(
            user=self.user, account=self.account, kind="expense", category=None,
            amount=Decimal(amount), fee_amount=Decimal("0.00"), date=day, description="t",
        )

    def test_24h_option_is_present_and_existing_options_are_preserved(self):
        values = [value for value, _label in TREND_PERIOD_CHOICES]
        self.assertEqual(values[0], "24h")
        for existing in ("7d", "30d", "3m", "6m", "12m", "this_year", "last_year", "all_time", "custom"):
            self.assertIn(existing, values)

    def test_24h_reports_todays_total_and_says_what_it_cannot_do(self):
        self._spend(TODAY, "12000.00")
        trend = spending_trend(self.user, "24h")
        self.assertEqual(trend["period"], "24h")
        self.assertEqual(trend["labels"], ["Today"])
        self.assertEqual(trend["totals"], [12000.0])
        self.assertEqual(trend["start"], TODAY.isoformat())
        self.assertEqual(trend["end"], TODAY.isoformat())
        self.assertEqual(trend["note"], TREND_24H_NOTE)

    def test_24h_excludes_yesterday_even_if_it_was_entered_just_now(self):
        self._spend(TODAY - datetime.timedelta(days=1), "9999.00")
        self._spend(TODAY, "1000.00")
        trend = spending_trend(self.user, "24h")
        self.assertEqual(trend["totals"], [1000.0])

    def test_24h_with_no_expenses_is_zero_not_missing(self):
        trend = spending_trend(self.user, "24h")
        self.assertEqual(trend["totals"], [0.0])

    def test_existing_periods_keep_their_own_shape(self):
        self.assertEqual(len(spending_trend(self.user, "7d")["labels"]), 7)
        self.assertEqual(len(spending_trend(self.user, "30d")["labels"]), 30)
        self.assertEqual(len(spending_trend(self.user, "6m")["labels"]), 6)
        self.assertIsNone(spending_trend(self.user, "7d")["note"])

    def test_endpoint_persists_the_choice_and_the_dashboard_remembers_it(self):
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.client.login(email="trend24@example.com", password="StrongPass123!")
        response = self.client.get(reverse("dashboard:trend_data"), {"period": "24h"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["period"], "24h")
        self.assertEqual(payload["labels"], ["Today"])
        self.assertIsNotNone(payload["note"])
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.dashboard_trend_period, "24h")
        page = self.client.get(reverse("dashboard:home"))
        self.assertContains(page, 'value="24h" selected')
        self.assertContains(page, "Last 24 Hours")

    def test_endpoint_is_scoped_to_the_logged_in_user(self):
        other = User.objects.create_user(email="trend24-other@example.com", password="StrongPass123!")
        other_account = FinancialAccount.objects.create(
            user=other, name="Theirs", account_type="cash", opening_balance=Decimal("0.00")
        )
        create_transaction(
            user=other, account=other_account, kind="expense", category=None,
            amount=Decimal("555555.00"), fee_amount=Decimal("0.00"), date=TODAY, description="theirs",
        )
        self.client.login(email="trend24@example.com", password="StrongPass123!")
        payload = self.client.get(reverse("dashboard:trend_data"), {"period": "24h"}).json()
        self.assertEqual(payload["totals"], [0.0])


class DefaultExpenseCategoryTests(TestCase):
    """The migration installs both categories for every user at once (they are
    global, user=None), so no per-user data migration is needed — and no user's
    own custom categories are touched."""

    def setUp(self):
        self.user = User.objects.create_user(email="cats@example.com", password="StrongPass123!")

    def test_charity_category_exists_globally(self):
        self.assertTrue(Category.objects.filter(user__isnull=True, name="Charity", kind="expense").exists())

    def test_shipping_fees_category_exists_globally(self):
        self.assertTrue(Category.objects.filter(user__isnull=True, name="Shipping Fees", kind="expense").exists())

    def test_no_duplicate_categories_even_after_reseeding(self):
        call_command("seed_categories", verbosity=0)
        call_command("seed_categories", verbosity=0)
        self.assertEqual(Category.objects.filter(user__isnull=True, name="Charity", kind="expense").count(), 1)
        self.assertEqual(Category.objects.filter(user__isnull=True, name="Shipping Fees", kind="expense").count(), 1)

    def test_both_categories_are_selectable_when_recording_an_expense(self):
        form = TransactionForm(user=self.user)
        names = set(form.fields["category"].queryset.values_list("name", flat=True))
        self.assertIn("Charity", names)
        self.assertIn("Shipping Fees", names)

    def test_existing_users_see_them_without_any_per_user_migration(self):
        other = User.objects.create_user(email="cats-existing@example.com", password="StrongPass123!")
        form = TransactionForm(user=other)
        names = set(form.fields["category"].queryset.values_list("name", flat=True))
        self.assertIn("Charity", names)
        self.assertIn("Shipping Fees", names)

    def test_a_users_own_custom_category_is_never_overwritten(self):
        custom = Category.objects.create(user=self.user, name="Charity", kind="expense")
        call_command("seed_categories", verbosity=0)
        self.assertTrue(Category.objects.filter(pk=custom.pk, user=self.user).exists())
        # The user's own one and the global one coexist; nothing was merged.
        self.assertEqual(Category.objects.filter(name="Charity", kind="expense").count(), 2)

    def test_charity_spending_appears_in_reports(self):
        account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("100000.00")
        )
        charity = Category.objects.get(user__isnull=True, name="Charity", kind="expense")
        create_transaction(
            user=self.user, account=account, kind="expense", category=charity,
            amount=Decimal("25000.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Donation",
        )
        report = compute_monthly_report(self.user, TODAY.year, TODAY.month)
        rows = {row["category__name"]: row["total"] for row in report["spending_by_category"]}
        self.assertEqual(rows["Charity"], Decimal("25000.00"))
        self.assertEqual(report["total_expenses"], Decimal("25000.00"))

    def test_shipping_fees_spending_appears_in_reports(self):
        account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("100000.00")
        )
        shipping = Category.objects.get(user__isnull=True, name="Shipping Fees", kind="expense")
        create_transaction(
            user=self.user, account=account, kind="expense", category=shipping,
            amount=Decimal("40000.00"), fee_amount=Decimal("0.00"), date=TODAY, description="Courier",
        )
        report = compute_monthly_report(self.user, TODAY.year, TODAY.month)
        rows = {row["category__name"]: row["total"] for row in report["spending_by_category"]}
        self.assertEqual(rows["Shipping Fees"], Decimal("40000.00"))

    def test_shipping_fees_are_not_the_same_as_transfer_charges(self):
        # A courier charge is an expense category; a bank/mobile-money charge
        # is a fee posting. Both coexist and stay distinct in reporting.
        account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("100000.00")
        )
        shipping = Category.objects.get(user__isnull=True, name="Shipping Fees", kind="expense")
        create_transaction(
            user=self.user, account=account, kind="expense", category=shipping,
            amount=Decimal("40000.00"), fee_amount=Decimal("750.00"), date=TODAY, description="Courier + charge",
        )
        report = compute_monthly_report(self.user, TODAY.year, TODAY.month)
        self.assertEqual(report["transaction_fees"], Decimal("750.00"))
        self.assertEqual(report["total_expenses"], Decimal("40750.00"))
