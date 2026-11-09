"""
Forecasting and savings/income calculators, including the "what if"
scenario engine (reduce spending / increase income / change deadline /
combine all three).

Every figure here is derived only from the user's own recorded history —
never invented — and all arithmetic stays in Decimal. Scenario adjustments
are purely hypothetical: they are never written back to a Transaction,
AccountPosting, or goal field. When there isn't enough history, or the
resulting savings capacity is zero or negative, the functions say so
explicitly instead of guessing a date.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

MIN_HISTORY_DAYS = 14
LOOKBACK_DAYS = 90
MONTH_DAYS = Decimal("30.44")  # average month length, used to convert daily <-> monthly figures
CENTS = Decimal("0.01")


def _q(value):
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


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


def _kind_filter(kind):
    from django.db.models import Q

    return Q(kind=kind)


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

    avg_income = _q(income / num_days)
    avg_expense = _q(expense / num_days)
    return avg_income, avg_expense, num_days, True


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


def _required_savings_for_deadline(remaining, deadline, current_pace):
    """Shared by the baseline savings-target calculator and every
    deadline-changing scenario, so "required daily/monthly savings" is
    computed exactly one way everywhere."""
    today = timezone.localdate()
    if deadline is None:
        return {
            "remaining_days": None, "remaining_months": None,
            "required_daily": None, "required_monthly": None, "shortfall": None,
        }

    remaining_days = max((deadline - today).days, 0)
    remaining_months = Decimal(remaining_days) / MONTH_DAYS

    required_daily = _q(remaining / remaining_days) if remaining_days > 0 else remaining
    required_monthly = _q(required_daily * MONTH_DAYS)
    shortfall = required_daily - current_pace
    shortfall = shortfall if shortfall > 0 else Decimal("0.00")

    return {
        "remaining_days": remaining_days,
        "remaining_months": remaining_months.quantize(Decimal("0.1")),
        "required_daily": required_daily,
        "required_monthly": required_monthly,
        "shortfall": shortfall,
    }


def savings_target_calculator(goal, deadline=None):
    deadline = deadline or goal.deadline
    remaining = goal.remaining()
    avg_income, avg_expense, num_days, has_history = income_expense_averages(goal.user)
    current_pace = avg_income - avg_expense if has_history else Decimal("0.00")

    result = {"remaining": remaining, "deadline": deadline, "current_pace_per_day": current_pace, "has_history": has_history}
    result.update(_required_savings_for_deadline(remaining, deadline, current_pace))
    return result


def estimate_completion_date(remaining, daily_capacity, *, achieved=False):
    """Shared completion-date estimator. Returns (date_or_None, message)."""
    if achieved or remaining <= 0:
        return None, "This goal has already been reached."
    if daily_capacity is None:
        return None, (
            f"Not enough transaction history yet (at least {MIN_HISTORY_DAYS} days of recorded "
            "activity is needed before a forecast can be estimated)."
        )
    if daily_capacity <= 0:
        return None, "This savings capacity is zero or negative, so no completion date can be estimated."
    days_needed = (remaining / daily_capacity).to_integral_value(rounding=ROUND_HALF_UP)
    return timezone.localdate() + timezone.timedelta(days=int(days_needed)), "Estimate — not a guarantee."


def goal_scenario(goal, *, reduce_expense_monthly=None, increase_income_monthly=None, deadline=None):
    """The 'what if' calculator behind Scenarios B/C/D/E. Pass only the
    adjustments relevant to the scenario being viewed — all are purely
    hypothetical and never touch real transactions, accounts, or the goal
    itself. Scenario A (current pace) is just this function with every
    adjustment left at its default."""
    reduce_expense_monthly = reduce_expense_monthly or Decimal("0.00")
    increase_income_monthly = increase_income_monthly or Decimal("0.00")

    avg_income, avg_expense, num_days, has_history = income_expense_averages(goal.user)
    remaining = goal.remaining()
    baseline_capacity = (avg_income - avg_expense) if has_history else None

    result = {
        "has_history": has_history,
        "history_days": num_days,
        "remaining": remaining,
        "achieved": goal.is_achieved(),
        "avg_daily_income": avg_income,
        "avg_daily_expense": avg_expense,
        "baseline_daily_capacity": baseline_capacity,
        "reduce_expense_monthly": reduce_expense_monthly,
        "increase_income_monthly": increase_income_monthly,
        "deadline": deadline,
        "is_hypothetical": bool(reduce_expense_monthly or increase_income_monthly or deadline),
    }

    if not has_history:
        result.update(adjusted_daily_capacity=None, estimated_completion_date=None,
                       message=f"Not enough transaction history yet (at least {MIN_HISTORY_DAYS} days needed).")
        return result

    daily_reduction = reduce_expense_monthly / MONTH_DAYS
    daily_increase = increase_income_monthly / MONTH_DAYS
    adjusted_income = avg_income + daily_increase
    # Expenses can be reduced hypothetically but never below zero.
    adjusted_expense = max(avg_expense - daily_reduction, Decimal("0.00"))
    adjusted_capacity = adjusted_income - adjusted_expense

    completion_date, message = estimate_completion_date(remaining, adjusted_capacity, achieved=result["achieved"])

    result.update(
        adjusted_daily_income=_q(adjusted_income),
        adjusted_daily_expense=_q(adjusted_expense),
        adjusted_daily_capacity=_q(adjusted_capacity),
        adjusted_monthly_capacity=_q(adjusted_capacity * MONTH_DAYS),
        estimated_completion_date=completion_date,
        message=message,
    )

    effective_deadline = deadline or goal.deadline
    if effective_deadline:
        target = _required_savings_for_deadline(remaining, effective_deadline, adjusted_capacity)
        target["deadline"] = effective_deadline
        if target["required_monthly"] is not None:
            target["required_minimum_monthly_income"] = _q(adjusted_expense * MONTH_DAYS) + target["required_monthly"]
        result["target_for_deadline"] = target

        if goal.deadline and effective_deadline != goal.deadline:
            baseline_target = _required_savings_for_deadline(remaining, goal.deadline, baseline_capacity or Decimal("0.00"))
            result["baseline_target_for_original_deadline"] = baseline_target

    return result


def required_income_calculator(user, goals=None, deadline_extension_months=0):
    """Estimates the monthly income needed to cover recorded average
    expenses plus the savings required for the given goals (or all active
    goals if none are given). `deadline_extension_months` lets this be
    recomputed as if every goal's deadline were pushed back N months
    (Scenario D applied across the whole plan), without changing any
    stored goal."""
    from dateutil.relativedelta import relativedelta

    from .models import SavingsGoal

    goals = goals if goals is not None else SavingsGoal.objects.filter(user=user, status=SavingsGoal.Status.ACTIVE)
    avg_income, avg_expense, num_days, has_history = income_expense_averages(user)
    current_monthly_income = _q(avg_income * MONTH_DAYS)
    required_monthly_expense = _q(avg_expense * MONTH_DAYS)

    required_monthly_savings = Decimal("0.00")
    for goal in goals:
        deadline = goal.deadline
        if deadline and deadline_extension_months:
            deadline = deadline + relativedelta(months=deadline_extension_months)
        calc = savings_target_calculator(goal, deadline=deadline)
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
        "additional_daily_income_needed": _q(additional_needed / MONTH_DAYS),
        "deadline_extension_months": deadline_extension_months,
    }
