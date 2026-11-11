from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import Sum

from apps.budgets.models import Budget
from apps.closing.services import balance_as_of
from apps.debts.models import Borrowing, BorrowingRepayment, Lending, LendingRepayment
from apps.goals.models import GoalAllocation
from apps.transactions.models import Transaction


def month_bounds(year, month):
    import datetime

    start = datetime.date(year, month, 1)
    end = start + relativedelta(months=1, days=-1)
    return start, end


def outstanding_borrowing_as_of(borrowing: Borrowing, as_of_date) -> Decimal:
    repaid = (
        BorrowingRepayment.objects.filter(borrowing=borrowing, date__lte=as_of_date).aggregate(total=Sum("amount"))[
            "total"
        ]
        or Decimal("0.00")
    )
    return borrowing.total_owed() - repaid


def outstanding_lending_as_of(lending: Lending, as_of_date) -> Decimal:
    received = (
        LendingRepayment.objects.filter(lending=lending, date__lte=as_of_date).aggregate(total=Sum("principal_amount"))[
            "total"
        ]
        or Decimal("0.00")
    )
    return lending.amount - received


def compute_monthly_report(user, year, month):
    start, end = month_bounds(year, month)
    prev_month_end = start - relativedelta(days=1)

    txns = Transaction.objects.filter(user=user, date__gte=start, date__lte=end)
    income_total = txns.filter(kind="income").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    expense_principal = txns.filter(kind="expense").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    fee_total = txns.aggregate(t=Sum("fee_amount"))["t"] or Decimal("0.00")
    expense_total = expense_principal + fee_total

    by_category = (
        txns.filter(kind="expense")
        .values("category__name")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )

    borrowed_this_month = (
        Borrowing.objects.filter(user=user, date_borrowed__gte=start, date_borrowed__lte=end).aggregate(t=Sum("amount"))["t"]
        or Decimal("0.00")
    )
    lent_this_month = (
        Lending.objects.filter(user=user, date_lent__gte=start, date_lent__lte=end).aggregate(t=Sum("amount"))["t"]
        or Decimal("0.00")
    )
    borrowing_repayments = (
        BorrowingRepayment.objects.filter(user=user, date__gte=start, date__lte=end).aggregate(t=Sum("amount"))["t"]
        or Decimal("0.00")
    )
    lending_repayments = (
        LendingRepayment.objects.filter(user=user, date__gte=start, date__lte=end).aggregate(t=Sum("principal_amount"))["t"]
        or Decimal("0.00")
    )

    opening_outstanding_debts = sum(
        (outstanding_borrowing_as_of(b, prev_month_end) for b in Borrowing.objects.filter(user=user, date_borrowed__lte=prev_month_end)),
        Decimal("0.00"),
    )
    closing_outstanding_debts = sum(
        (outstanding_borrowing_as_of(b, end) for b in Borrowing.objects.filter(user=user, date_borrowed__lte=end)),
        Decimal("0.00"),
    )
    opening_outstanding_receivables = sum(
        (outstanding_lending_as_of(l, prev_month_end) for l in Lending.objects.filter(user=user, date_lent__lte=prev_month_end)),
        Decimal("0.00"),
    )
    closing_outstanding_receivables = sum(
        (outstanding_lending_as_of(l, end) for l in Lending.objects.filter(user=user, date_lent__lte=end)),
        Decimal("0.00"),
    )

    budgets = Budget.objects.filter(user=user, month=start).select_related("category")
    savings_contributions = (
        GoalAllocation.objects.filter(user=user, date__gte=start, date__lte=end, amount__gt=0).aggregate(t=Sum("amount"))["t"]
        or Decimal("0.00")
    )

    return {
        "year": year,
        "month": month,
        "start": start,
        "end": end,
        "total_income": income_total,
        "total_expenses": expense_total,
        "net": income_total - expense_total,
        "mobile_money_fees": fee_total,
        "spending_by_category": list(by_category),
        "largest_categories": list(by_category[:5]),
        "opening_balance": balance_as_of(user, prev_month_end),
        "closing_balance": balance_as_of(user, end),
        "balance_unavailable": balance_as_of(user, prev_month_end) is None or balance_as_of(user, end) is None,
        "money_borrowed": borrowed_this_month,
        "money_lent": lent_this_month,
        "loan_repayments_paid": borrowing_repayments,
        "loan_repayments_received": lending_repayments,
        "opening_outstanding_debts": opening_outstanding_debts,
        "closing_outstanding_debts": closing_outstanding_debts,
        "opening_outstanding_receivables": opening_outstanding_receivables,
        "closing_outstanding_receivables": closing_outstanding_receivables,
        "budgets": budgets,
        "savings_contributions": savings_contributions,
    }
