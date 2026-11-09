from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from apps.accounts.models import FinancialAccount
from apps.accounts.services import bulk_account_balances
from apps.bills.models import RecurringBill
from apps.budgets.models import Budget
from apps.closing.services import day_breakdown
from apps.debts.models import Borrowing, Lending
from apps.goals.models import SavingsGoal
from apps.goals.services import savings_target_calculator
from apps.reports.services import compute_monthly_report


def health_warnings(user, report, goals):
    warnings = []
    if report["total_expenses"] > report["total_income"] and report["total_income"] > 0:
        warnings.append("Your expenses exceeded your income this month.")

    overdue_borrowings = [b for b in Borrowing.objects.filter(user=user, status=Borrowing.Status.ACTIVE) if b.is_overdue()]
    if overdue_borrowings:
        warnings.append(f"You have {len(overdue_borrowings)} overdue repayment(s) to lenders.")

    overdue_lendings = [l for l in Lending.objects.filter(user=user, status=Lending.Status.ACTIVE) if l.is_overdue()]
    if overdue_lendings:
        warnings.append(f"{len(overdue_lendings)} borrower(s) have overdue repayments owed to you.")

    for budget in Budget.objects.filter(user=user, month=report["start"]):
        if budget.percent_used() >= 100:
            warnings.append(f"Your '{budget.category.name}' budget is fully used ({budget.percent_used()}%).")

    for goal in goals:
        if goal.status != SavingsGoal.Status.ACTIVE or not goal.deadline:
            continue
        calc = savings_target_calculator(goal)
        if calc["shortfall"] and calc["shortfall"] > 0:
            warnings.append(f"Your current savings pace is below target for '{goal.name}'.")

    return warnings


def spending_trend(user, months=6):
    from apps.transactions.models import Transaction
    from django.db.models import Sum

    today = timezone.localdate()
    labels, totals = [], []
    for i in range(months - 1, -1, -1):
        month_start = (today.replace(day=1)) - relativedelta(months=i)
        month_end = month_start + relativedelta(months=1, days=-1)
        total = (
            Transaction.objects.filter(user=user, kind="expense", date__gte=month_start, date__lte=month_end).aggregate(
                t=Sum("amount")
            )["t"]
            or Decimal("0.00")
        )
        labels.append(month_start.strftime("%b %Y"))
        totals.append(float(total))
    return labels, totals


def compute_dashboard(user):
    today = timezone.localdate()
    accounts = FinancialAccount.objects.filter(user=user, status=FinancialAccount.Status.ACTIVE)
    balances = bulk_account_balances(user, accounts)
    total_balance = sum(balances.values(), Decimal("0.00"))

    today_breakdown = day_breakdown(user, today)
    report = compute_monthly_report(user, today.year, today.month)

    goals = list(SavingsGoal.objects.filter(user=user, status=SavingsGoal.Status.ACTIVE))
    upcoming_bills = [b for b in RecurringBill.objects.filter(user=user, status=RecurringBill.Status.ACTIVE) if b.is_due_soon(7) or b.is_overdue()]
    overdue_bills = [b for b in upcoming_bills if b.is_overdue()]

    warnings = health_warnings(user, report, goals)
    labels, trend_totals = spending_trend(user)

    return {
        "today": today,
        "accounts": accounts,
        "balances": balances,
        "total_balance": total_balance,
        "today_received": today_breakdown["received"],
        "today_spent": today_breakdown["spent"],
        "today_net": today_breakdown["net"],
        "monthly_income": report["total_income"],
        "monthly_expenses": report["total_expenses"],
        "money_owed_by_me": sum((b.outstanding() for b in Borrowing.objects.filter(user=user, status=Borrowing.Status.ACTIVE)), Decimal("0.00")),
        "money_owed_to_me": sum((l.outstanding() for l in Lending.objects.filter(user=user, status=Lending.Status.ACTIVE)), Decimal("0.00")),
        "upcoming_bills": upcoming_bills,
        "overdue_bills_count": len(overdue_bills),
        "goals": goals,
        "warnings": warnings,
        "trend_labels": labels,
        "trend_totals": trend_totals,
    }
