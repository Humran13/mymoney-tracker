import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import FinancialAccount
from apps.bills.models import RecurringBill
from apps.debts.models import Borrowing, Lending
from apps.goals.models import SavingsGoal
from apps.transactions.models import Category
from apps.transactions.services import create_transaction
from apps.users.models import User


class SmokePageLoadTests(TestCase):
    """Every major authenticated page must render without error — this
    catches template/URL bugs that unit tests of pure services miss."""

    def setUp(self):
        self.user = User.objects.create_user(email="smoke@example.com", password="StrongPass123!")
        self.user.profile.first_time_setup_complete = True
        self.user.profile.save()
        self.account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("100000.00")
        )
        self.category = Category.objects.create(user=None, name="Transport", kind="expense")
        create_transaction(
            user=self.user, account=self.account, kind="expense", category=self.category,
            amount=Decimal("5000.00"), fee_amount=Decimal("200.00"), date=datetime.date.today(), description="Boda",
        )
        self.borrowing = Borrowing.objects.create(
            user=self.user, lender_name="John", amount=Decimal("10000.00"),
            date_borrowed=datetime.date.today(), receiving_account=self.account,
        )
        self.lending = Lending.objects.create(
            user=self.user, borrower_name="Sarah", amount=Decimal("10000.00"),
            date_lent=datetime.date.today(), source_account=self.account,
        )
        self.bill = RecurringBill.objects.create(
            user=self.user, name="Internet", amount=Decimal("80000.00"), category=self.category,
            payment_account=self.account, frequency="monthly", next_due_date=datetime.date.today(),
        )
        self.goal = SavingsGoal.objects.create(user=self.user, name="Laptop", target_amount=Decimal("1000000.00"))
        self.client.login(email="smoke@example.com", password="StrongPass123!")

    def _assert_ok(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"{url} returned {response.status_code}")

    def test_core_pages_render(self):
        self._assert_ok(reverse("dashboard:home"))
        self._assert_ok(reverse("accounts:list"))
        self._assert_ok(reverse("accounts:create"))
        self._assert_ok(reverse("accounts:transfer_list"))
        self._assert_ok(reverse("accounts:transfer_create"))
        self._assert_ok(reverse("transactions:list"))
        self._assert_ok(reverse("transactions:create"))
        self._assert_ok(reverse("transactions:template_list"))
        self._assert_ok(reverse("debts:dashboard"))
        self._assert_ok(reverse("debts:borrowing_detail", args=[self.borrowing.pk]))
        self._assert_ok(reverse("debts:borrowing_create"))
        self._assert_ok(reverse("debts:borrowing_reclassify", args=[self.borrowing.pk]))
        self._assert_ok(reverse("debts:lending_detail", args=[self.lending.pk]))
        self._assert_ok(reverse("bills:list"))
        self._assert_ok(reverse("bills:pay", args=[self.bill.pk]))
        self.assertEqual(self.client.get(reverse("closing:today")).status_code, 302)
        self._assert_ok(reverse("closing:detail", args=[datetime.date.today().isoformat()]))
        self._assert_ok(reverse("closing:history"))
        self._assert_ok(reverse("budgets:list"))
        self._assert_ok(reverse("budgets:create"))
        self._assert_ok(reverse("goals:list"))
        self._assert_ok(reverse("goals:detail", args=[self.goal.pk]))
        self._assert_ok(reverse("goals:required_income"))
        self._assert_ok(reverse("reports:monthly"))
        self._assert_ok(reverse("reports:monthly_csv"))
        self._assert_ok(reverse("reports:monthly_pdf"))
        self._assert_ok(reverse("reports:income_vs_expenses"))
        self._assert_ok(reverse("reports:income_vs_expenses_day", args=[datetime.date.today().isoformat()]))
        self._assert_ok(reverse("accounts:reconciliation"))
        self._assert_ok(reverse("users:profile"))
        self._assert_ok(reverse("users:settings"))
        self._assert_ok(reverse("health_check"))
        self._assert_ok(reverse("feedback:list"))
        self._assert_ok(reverse("feedback:create"))

    def test_admin_pages_render_for_staff(self):
        self.user.is_staff = True
        self.user.save()
        self._assert_ok(reverse("adminpanel:dashboard"))
        self._assert_ok(reverse("adminpanel:user_list"))
        self._assert_ok(reverse("adminpanel:user_detail", args=[self.user.pk]))
        self._assert_ok(reverse("adminpanel:audit_log"))
        self._assert_ok(reverse("adminpanel:site_settings"))
        self._assert_ok(reverse("adminpanel:system_health"))
        self._assert_ok(reverse("adminpanel:backups"))
        self._assert_ok(reverse("adminpanel:feedback_list"))
