import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import AccountPosting, FinancialAccount
from apps.goals import services as goal_services
from apps.goals.models import SavingsGoal
from apps.transactions.services import create_transaction
from apps.users.models import User


class GoalScenarioTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="scenario@example.com", password="StrongPass123!")
        self.account = FinancialAccount.objects.create(
            user=self.user, name="Cash", account_type="cash", opening_balance=Decimal("0.00")
        )
        # 20 days of history: 50,000/day income, 30,000/day expense -> capacity 20,000/day.
        today = datetime.date.today()
        for i in range(20):
            day = today - datetime.timedelta(days=i)
            create_transaction(user=self.user, account=self.account, kind="income", category=None,
                                amount=Decimal("50000.00"), fee_amount=Decimal("0.00"), date=day, description="")
            create_transaction(user=self.user, account=self.account, kind="expense", category=None,
                                amount=Decimal("30000.00"), fee_amount=Decimal("0.00"), date=day, description="")
        self.goal = SavingsGoal.objects.create(user=self.user, name="Laptop", target_amount=Decimal("2000000.00"))

    def test_baseline_scenario_matches_forecast(self):
        scenario = goal_services.goal_scenario(self.goal)
        forecast = goal_services.forecast_goal(self.goal)
        self.assertEqual(scenario["adjusted_daily_capacity"], forecast["daily_capacity"])
        self.assertEqual(scenario["estimated_completion_date"], forecast["estimated_completion_date"])
        self.assertFalse(scenario["is_hypothetical"])

    def test_reducing_spending_increases_capacity_and_moves_date_earlier(self):
        baseline = goal_services.goal_scenario(self.goal)
        reduced = goal_services.goal_scenario(self.goal, reduce_expense_monthly=Decimal("300000.00"))
        self.assertGreater(reduced["adjusted_daily_capacity"], baseline["adjusted_daily_capacity"])
        self.assertLess(reduced["estimated_completion_date"], baseline["estimated_completion_date"])

    def test_increasing_income_increases_capacity(self):
        baseline = goal_services.goal_scenario(self.goal)
        boosted = goal_services.goal_scenario(self.goal, increase_income_monthly=Decimal("500000.00"))
        self.assertGreater(boosted["adjusted_daily_capacity"], baseline["adjusted_daily_capacity"])

    def test_expense_cannot_be_reduced_below_zero(self):
        # Daily expense is 30,000; asking to cut 10,000,000/month (~328,000/day) must floor at 0, not go negative.
        scenario = goal_services.goal_scenario(self.goal, reduce_expense_monthly=Decimal("10000000.00"))
        self.assertEqual(scenario["adjusted_daily_expense"], Decimal("0.00"))

    def test_deadline_scenario_computes_required_savings(self):
        deadline = datetime.date.today() + datetime.timedelta(days=100)
        scenario = goal_services.goal_scenario(self.goal, deadline=deadline)
        target = scenario["target_for_deadline"]
        expected_daily = (Decimal("2000000.00") / 100).quantize(Decimal("0.01"))
        self.assertEqual(target["required_daily"], expected_daily)
        self.assertEqual(target["remaining_days"], 100)

    def test_combined_scenario_applies_all_adjustments_together(self):
        deadline = datetime.date.today() + datetime.timedelta(days=50)
        scenario = goal_services.goal_scenario(
            self.goal,
            reduce_expense_monthly=Decimal("150000.00"),
            increase_income_monthly=Decimal("150000.00"),
            deadline=deadline,
        )
        # capacity should reflect both the income bump and the expense cut.
        self.assertGreater(scenario["adjusted_daily_capacity"], scenario["baseline_daily_capacity"])
        self.assertIn("target_for_deadline", scenario)
        self.assertEqual(scenario["target_for_deadline"]["remaining_days"], 50)

    def test_scenarios_never_write_any_ledger_posting(self):
        before = AccountPosting.objects.count()
        goal_services.goal_scenario(
            self.goal, reduce_expense_monthly=Decimal("50000"), increase_income_monthly=Decimal("50000"),
            deadline=datetime.date.today() + datetime.timedelta(days=30),
        )
        goal_services.required_income_calculator(self.user, deadline_extension_months=6)
        self.assertEqual(AccountPosting.objects.count(), before)

    def test_insufficient_history_guardrail_applies_to_scenarios_too(self):
        fresh_user = User.objects.create_user(email="fresh@example.com", password="StrongPass123!")
        goal = SavingsGoal.objects.create(user=fresh_user, name="Car", target_amount=Decimal("1000000.00"))
        scenario = goal_services.goal_scenario(goal, increase_income_monthly=Decimal("1000000.00"))
        self.assertFalse(scenario["has_history"])
        self.assertIsNone(scenario["estimated_completion_date"])

    def test_required_income_with_extended_deadlines_reduces_required_monthly_savings(self):
        goal = SavingsGoal.objects.create(
            user=self.user, name="House", target_amount=Decimal("5000000.00"),
            deadline=datetime.date.today() + datetime.timedelta(days=100),
        )
        baseline = goal_services.required_income_calculator(self.user, goals=[goal])
        extended = goal_services.required_income_calculator(self.user, goals=[goal], deadline_extension_months=12)
        self.assertLess(extended["required_monthly_savings"], baseline["required_monthly_savings"])
