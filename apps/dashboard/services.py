from datetime import date, timedelta
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


TREND_PERIOD_CHOICES = [
    ("7d", "Last 7 Days"),
    ("30d", "Last 30 Days"),
    ("3m", "Last 3 Months"),
    ("6m", "Last 6 Months"),
    ("12m", "Last 12 Months"),
    ("this_year", "This Year"),
    ("last_year", "Last Year"),
    ("all_time", "All Time"),
    ("custom", "Custom Date Range"),
]
TREND_PERIOD_LABELS = dict(TREND_PERIOD_CHOICES)
TREND_DEFAULT_PERIOD = "6m"


def spending_trend(user, period=TREND_DEFAULT_PERIOD, start=None, end=None):
    """Expense totals for the selected period, bucketed by day/month/year
    depending on the span. Buckets with no expenses are included (as 0),
    never skipped — the trend line must show a full, continuous period."""
    from django.db.models import Sum

    from apps.transactions.models import Transaction

    today = timezone.localdate()

    if period == "custom" and start and end and start <= end:
        start_date, end_date = start, end
    else:
        period = period if period in TREND_PERIOD_LABELS and period != "custom" else TREND_DEFAULT_PERIOD
        start_date = end_date = None

    if period == "7d":
        end_date, start_date = today, today - timedelta(days=6)
    elif period == "30d":
        end_date, start_date = today, today - timedelta(days=29)
    elif period == "3m":
        end_date = today
        start_date = today.replace(day=1) - relativedelta(months=2)
    elif period == "6m":
        end_date = today
        start_date = today.replace(day=1) - relativedelta(months=5)
    elif period == "12m":
        end_date = today
        start_date = today.replace(day=1) - relativedelta(months=11)
    elif period == "this_year":
        end_date = today
        start_date = today.replace(month=1, day=1)
    elif period == "last_year":
        start_date = today.replace(year=today.year - 1, month=1, day=1)
        end_date = today.replace(year=today.year - 1, month=12, day=31)
    elif period == "all_time":
        first_tx = Transaction.objects.filter(user=user).order_by("date").first()
        start_date = first_tx.date if first_tx else today
        end_date = today
    # else: period == "custom" — start_date/end_date already set above

    span_days = (end_date - start_date).days

    if period in ("7d", "30d"):
        granularity = "day"
    elif period == "custom":
        granularity = "day" if span_days <= 62 else "month" if span_days <= 730 else "year"
    elif period == "all_time":
        granularity = "year" if span_days > 730 else "month"
    else:
        granularity = "month"

    labels, totals = [], []
    base_qs = Transaction.objects.filter(user=user, kind="expense")

    if granularity == "day":
        d = start_date
        while d <= end_date:
            total = base_qs.filter(date=d).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
            labels.append(d.strftime("%d %b"))
            totals.append(float(total))
            d += timedelta(days=1)
    elif granularity == "month":
        cur = start_date.replace(day=1)
        end_month = end_date.replace(day=1)
        while cur <= end_month:
            bucket_end = min(cur + relativedelta(months=1, days=-1), end_date)
            total = base_qs.filter(date__gte=cur, date__lte=bucket_end).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
            labels.append(cur.strftime("%b %Y"))
            totals.append(float(total))
            cur += relativedelta(months=1)
    else:  # year
        for year in range(start_date.year, end_date.year + 1):
            bucket_start = max(date(year, 1, 1), start_date)
            bucket_end = min(date(year, 12, 31), end_date)
            total = base_qs.filter(date__gte=bucket_start, date__lte=bucket_end).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
            labels.append(str(year))
            totals.append(float(total))

    if period == "custom":
        title = f"Spending trend ({start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')})"
    else:
        title = f"Spending trend ({TREND_PERIOD_LABELS[period]})"

    return {
        "labels": labels,
        "totals": totals,
        "title": title,
        "period": period,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
    }


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
    profile = user.profile
    trend = spending_trend(
        user,
        profile.dashboard_trend_period or TREND_DEFAULT_PERIOD,
        profile.dashboard_trend_start,
        profile.dashboard_trend_end,
    )

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
        "trend_labels": trend["labels"],
        "trend_totals": trend["totals"],
        "trend_title": trend["title"],
        "trend_period": trend["period"],
        "trend_start": profile.dashboard_trend_start,
        "trend_end": profile.dashboard_trend_end,
        "trend_period_choices": TREND_PERIOD_CHOICES,
    }
