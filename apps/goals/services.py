"""
Forecasting and savings/income calculators.

Every figure here is derived only from the user's own recorded history —
never invented — and all arithmetic stays in Decimal. When there isn't
enough history, or the user's savings capacity is zero or negative, the
functions say so explicitly instead of guessing a date.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

MIN_HISTORY_DAYS = 14
LOOKBACK_DAYS = 90


def _activity_window(user):
    from apps.transactions.models import Transaction

    earliest = Transaction.objects.filter(user=user).order_by("date").values_list("date", flat=True).first()
    today = timezone.localdate()
    if earliest is None:
        return None, today, 0
    lookback_start = today - timezone.timedelta(days=LOOKBACK_DAYS)
    start = max(earliest, lookback_start)
    num_days = (today - start).days + 1
    return start, today, num_days


def income_expense_averages(user):
    """Returns (avg_daily_income, avg_daily_expense, num_days, has_enough_history)."""
    from apps.transactions.models import Transaction

    start, end, num_days = _activity_window(user)
    if start is None or num_days < MIN_HISTORY_DAYS:
        return Decimal("0.00"), Decimal("0.00"), num_days, False

    totals = Transaction.objects.filter(user=user, date__gte=start, date__lte=end).aggregate(
        income=Sum("amount", filter=_kind_filter("income")),
        expense_principal=Sum("amount", filter=_kind_filter("expense")),
        expense_fees=Sum("fee_amount"),
    )
    income = totals["income"] or Decimal("0.00")
    expense = (totals["expense_principal"] or Decimal("0.00")) + (totals["expense_fees"] or Decimal("0.00"))

    avg_income = (income / num_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    avg_expense = (expense / num_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return avg_income, avg_expense, num_days, True


def _kind_filter(kind):
    from django.db.models import Q

    return Q(kind=kind)


def forecast_goal(goal):
    avg_income, avg_expense, num_days, has_history = income_expense_averages(goal.user)
    remaining = goal.remaining()

    result = {
        "avg_daily_income": avg_income,
        "avg_daily_expense": avg_expense,
        "daily_capacity": avg_income - avg_expense,
        "history_days": num_days,
        "remaining": remaining,
        "achieved": goal.is_achieved(),
        "estimated_completion_date": None,
        "message": None,
    }

    if result["achieved"]:
        result["message"] = "This goal has already been reached."
        return result

    if not has_history:
        result["message"] = (
            f"Not enough transaction history yet (at least {MIN_HISTORY_DAYS} days of recorded "
            "activity is needed before a forecast can be estimated)."
        )
        return result

    daily_capacity = result["daily_capacity"]
    if daily_capacity <= 0:
        result["message"] = (
            "Your recorded spending has matched or exceeded your income recently, so a completion "
            "date can't be estimated until your savings capacity is positive."
        )
        return result

    days_needed = (remaining / daily_capacity).to_integral_value(rounding=ROUND_HALF_UP)
    result["estimated_completion_date"] = timezone.localdate() + timezone.timedelta(days=int(days_needed))
    result["message"] = f"Estimate based on your recorded activity over the last {num_days} days."
    return result


def savings_target_calculator(goal, deadline=None):
    deadline = deadline or goal.deadline
    today = timezone.localdate()
    remaining = goal.remaining()
    avg_income, avg_expense, num_days, has_history = income_expense_averages(goal.user)
    current_pace = avg_income - avg_expense if has_history else Decimal("0.00")

    result = {
        "remaining": remaining,
        "deadline": deadline,
        "current_pace_per_day": current_pace,
        "has_history": has_history,
    }

    if deadline is None:
        result.update(remaining_days=None, remaining_months=None, required_daily=None, required_monthly=None, shortfall=None)
        return result

    remaining_days = max((deadline - today).days, 0)
    remaining_months = Decimal(remaining_days) / Decimal("30.44")

    required_daily = (remaining / remaining_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if remaining_days > 0 else remaining
    required_monthly = (required_daily * Decimal("30.44")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    shortfall = required_daily - current_pace
    shortfall = shortfall if shortfall > 0 else Decimal("0.00")

    result.update(
        remaining_days=remaining_days,
        remaining_months=remaining_months.quantize(Decimal("0.1")),
        required_daily=required_daily,
        required_monthly=required_monthly,
        shortfall=shortfall,
    )
    return result


def required_income_calculator(user, goals=None, deadline_extension_months=0):
    """Estimates the monthly income needed to cover recorded average
    expenses plus the savings required for the given goals (or all active
    goals if none are given)."""
    from .models import SavingsGoal

    goals = goals if goals is not None else SavingsGoal.objects.filter(user=user, status=SavingsGoal.Status.ACTIVE)
    avg_income, avg_expense, num_days, has_history = income_expense_averages(user)
    current_monthly_income = (avg_income * Decimal("30.44")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    required_monthly_expense = (avg_expense * Decimal("30.44")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    required_monthly_savings = Decimal("0.00")
    for goal in goals:
        calc = savings_target_calculator(goal)
        if calc["required_monthly"]:
            required_monthly_savings += calc["required_monthly"]

    required_monthly_income = required_monthly_expense + required_monthly_savings
    additional_needed = required_monthly_income - current_monthly_income
    additional_needed = additional_needed if additional_needed > 0 else Decimal("0.00")

    return {
        "has_history": has_history,
        "history_days": num_days,
        "current_monthly_income": current_monthly_income,
        "required_monthly_expense": required_monthly_expense,
        "required_monthly_savings": required_monthly_savings,
        "required_monthly_income": required_monthly_income,
        "additional_monthly_income_needed": additional_needed,
        "additional_daily_income_needed": (additional_needed / Decimal("30.44")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    }
