import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import FinancialAccount
from apps.accounts.services import account_balance
from apps.bills import services as bill_services
from apps.bills.models import RecurringBill
from apps.budgets.models import Budget
from apps.goals import services as goal_services
from apps.goals.models import GoalAllocation, SavingsGoal
from apps.transactions.models import Category
from apps.transactions.services import create_transaction
from apps.users.models import User


class RecurringBillTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="bill@example.com", password="StrongPass123!")
        self.account = FinancialAccount.objects.create(
            user=self.user, name="Bank", account_type="bank", opening_balance=Decimal("200000.00"),
            opening_balance_date=datetime.date(2024, 1, 1),
        )
        self.category = Category.objects.create(user=None, name="Rent", kind="expense")
        self.bill = RecurringBill.objects.create(
            user=self.user, name="Rent", amount=Decimal("150000.00"), category=self.category,
            payment_account=self.account, frequency="monthly", next_due_date=datetime.date(2024, 1, 1),
        )

    def test_paying_bill_creates_expense_and_advances_due_date(self):
        bill_services.pay_bill(user=self.user, bill=self.bill, paid_date=datetime.date(2024, 1, 1))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.next_due_date, datetime.date(2024, 2, 1))
        self.assertEqual(account_balance(self.account), Decimal("50000.00"))

    def test_cannot_pay_the_same_bill_cycle_twice(self):
        bill_services.pay_bill(user=self.user, bill=self.bill, paid_date=datetime.date(2024, 1, 1))
        self.bill.refresh_from_db()
        self.bill.next_due_date = datetime.date(2024, 1, 1)  # simulate a double-submit before advancing
        self.bill.save()
        with self.assertRaises(ValueError):
            bill_services.pay_bill(user=self.user, bill=self.bill, paid_date=datetime.date(2024, 1, 1))


class BudgetTests(TestCase):
    def test_percent_used_reflects_actual_spending(self):
        user = User.objects.create_user(email="budget@example.com", password="StrongPass123!")
        account = FinancialAccount.objects.create(user=user, name="Cash", account_type="cash", opening_balance=Decimal("0"))
        category = Category.objects.create(user=None, name="Transport", kind="expense")
        budget = Budget.objects.create(user=user, category=category, month=datetime.date(2024, 3, 1), amount=Decimal("100000.00"))
        create_transaction(
            user=user, account=account, kind="expense", category=category,
            amount=Decimal("40000.00"), fee_amount=Decimal("0.00"), date=datetime.date(2024, 3, 10), description="bus",
        )
        self.assertEqual(budget.spent(), Decimal("40000.00"))
        self.assertEqual(budget.percent_used(), 40)


class GoalForecastTests(TestCase):
    def test_forecast_declines_without_enough_history(self):
        user = User.objects.create_user(email="goal@example.com", password="StrongPass123!")
        goal = SavingsGoal.objects.create(user=user, name="Laptop", target_amount=Decimal("1000000.00"))
        forecast = goal_services.forecast_goal(goal)
        self.assertIsNone(forecast["estimated_completion_date"])
        self.assertIn("Not enough", forecast["message"])

    def test_goal_never_auto_created_and_allocations_do_not_touch_real_balances(self):
        user = User.objects.create_user(email="goal2@example.com", password="StrongPass123!")
        self.assertEqual(SavingsGoal.objects.filter(user=user).count(), 0)
        account = FinancialAccount.objects.create(user=user, name="Cash", account_type="cash", opening_balance=Decimal("500000.00"))
        goal = SavingsGoal.objects.create(user=user, name="Emergency Fund", target_amount=Decimal("200000.00"))
        GoalAllocation.objects.create(user=user, goal=goal, amount=Decimal("100000.00"), date=datetime.date.today())
        self.assertEqual(goal.current_saved(), Decimal("100000.00"))
        self.assertEqual(account_balance(account), Decimal("500000.00"))  # unaffected: allocation is virtual
