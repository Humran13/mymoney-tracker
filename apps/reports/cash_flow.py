"""Daily/weekly income-vs-expenses and cash-flow analysis (Phase 13).

Two different questions, deliberately never mixed into one number:

1. **Cash in vs cash out** — what actually moved in and out of the user's own
   accounts on a day. This is what "Money received / Money spent" means on the
   dashboard and in this report.
2. **Income earned vs expenses incurred** — the accrual view from
   `compute_monthly_report` (a sale on credit is income when it happens, a
   purchase on credit is an expense when it happens, whether or not cash moved).

A cash loan is cash in but *not* income; a goods-on-credit purchase is an
expense but *not* cash out; repaying a loan principal is cash out but *not* an
extra expense. Keeping the two statements apart is what stops those from being
silently conflated.

Everything here is single-user scoped (rule 14) and Decimal-only.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import AccountPosting, FinancialAccount, Transfer
from apps.debts.models import Borrowing, BorrowingRepayment, Lending, LendingRepayment
from apps.transactions.models import Transaction

ZERO = Decimal("0.00")

#: Postings that put money into the user's own accounts.
CASH_IN_TYPES = frozenset(
    {
        AccountPosting.SourceType.INCOME,
        AccountPosting.SourceType.BORROW_RECEIVE,
        AccountPosting.SourceType.LEND_REPAY,
    }
)

#: Postings that take money out of the user's own accounts.
CASH_OUT_TYPES = frozenset(
    {
        AccountPosting.SourceType.EXPENSE,
        AccountPosting.SourceType.FEE,
        AccountPosting.SourceType.BORROW_REPAY,
        AccountPosting.SourceType.LEND_SEND,
        AccountPosting.SourceType.BILL_PAYMENT,
    }
)

PRESET_CHOICES = [
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("date", "Select Specific Date"),
    ("7d", "Last 7 Days"),
    ("30d", "Last 30 Days"),
    ("60d", "Last 60 Days"),
    ("custom", "Custom Date Range"),
]
PRESET_LABELS = dict(PRESET_CHOICES)
DEFAULT_PRESET = "today"


def _fmt(d: date) -> str:
    return d.strftime("%d %b %Y")


def resolve_period(preset, *, on=None, start=None, end=None, today=None) -> dict:
    """Turn the filter widgets into a validated (start, end) window.

    Returns an `error` (and falls back to today) rather than raising, so a
    hand-edited or half-submitted URL still renders a usable page instead of
    a 500. Africa/Kampala comes from `TIME_ZONE`; `timezone.localdate()`
    supplies "today" in that zone.
    """
    if today is None:
        from django.utils import timezone

        today = timezone.localdate()
    preset = preset if preset in PRESET_LABELS else DEFAULT_PRESET
    error = None

    if preset == "today":
        start_date = end_date = today
    elif preset == "yesterday":
        start_date = end_date = today - timedelta(days=1)
    elif preset == "date":
        if on is None:
            start_date = end_date = today
        elif on > today:
            error = "That date is in the future, so today is shown instead."
            start_date = end_date = today
        else:
            start_date = end_date = on
    elif preset in ("7d", "30d", "60d"):
        span = {"7d": 7, "30d": 30, "60d": 60}[preset]
        end_date = today
        start_date = today - timedelta(days=span - 1)
    else:  # custom
        if start and end and start <= end:
            start_date, end_date = start, end
        elif start and end:
            error = "The start date is after the end date, so today is shown instead."
            start_date = end_date = today
        else:
            error = "Pick both a start and an end date for a custom range. Today is shown for now."
            start_date = end_date = today

    if preset == "today":
        label = "Today"
    elif preset == "yesterday":
        label = "Yesterday"
    elif start_date == end_date:
        label = _fmt(start_date)
    else:
        label = f"{_fmt(start_date)} – {_fmt(end_date)}"

    return {
        "preset": preset,
        "start": start_date,
        "end": end_date,
        "label": label,
        "error": error,
        "is_single_day": start_date == end_date,
        "days": (end_date - start_date).days + 1,
    }


def _windowed_postings(user, start, end):
    """Postings in the window that are inside each account's opening-balance
    snapshot.

    A posting dated before its account's starting date is already reflected in
    that account's opening balance — it is a historical record of something that
    happened before tracking started, not a movement of the money currently
    being tracked. Including it here would make the report contradict the
    balance it is supposed to explain (and would re-count a historical loan the
    user only remembered and entered later). The starting date is the account's
    declared `opening_balance_date`, or — when none was declared — the day the
    account was created (see `apps.accounts.models.balance_start_date`).
    """
    qs = AccountPosting.objects.filter(user=user, occurred_on__gte=start, occurred_on__lte=end)
    cutoffs = FinancialAccount.objects.filter(user=user).values_list("id", "opening_balance_date", "created_at")
    for account_id, declared, created in cutoffs:
        start_date = declared
        if start_date is None and created is not None:
            start_date = timezone.localtime(created).date() if timezone.is_aware(created) else created.date()
        if start_date is not None:
            qs = qs.exclude(account_id=account_id, occurred_on__lt=start_date)
    return qs


def _split_totals(postings) -> dict:
    received = postings.filter(source_type__in=CASH_IN_TYPES).aggregate(t=Sum("amount"))["t"] or ZERO
    spent = postings.filter(source_type__in=CASH_OUT_TYPES).aggregate(t=Sum("amount"))["t"] or ZERO
    return {"received": received, "spent": -spent, "difference": received + spent}


def describe_difference(received: Decimal, spent: Decimal) -> str:
    """Plain-language summary. Shortfall, surplus, exactly even and "nothing
    recorded" each get their own sentence — a day with no records must never
    look like a balanced day."""
    difference = received - spent
    if received == 0 and spent == 0:
        return "No money was recorded in or out for this period."
    if difference < 0:
        return f"You spent {abs(difference):,.2f} more than you received."
    if difference > 0:
        return f"You received {difference:,.2f} more than you spent."
    return "Money received and money spent were exactly equal."


def period_cash_flow(user, start, end) -> dict:
    """Cash in / cash out for the window, plus a per-day breakdown that always
    contains every day (zero-activity days included, never skipped)."""
    postings = _windowed_postings(user, start, end)
    totals = _split_totals(postings)

    by_type = {
        row["source_type"]: row["total"]
        for row in postings.filter(source_type__in=CASH_IN_TYPES | CASH_OUT_TYPES)
        .values("source_type")
        .annotate(total=Sum("amount"))
    }

    daily = {}
    for row in (
        postings.filter(source_type__in=CASH_IN_TYPES | CASH_OUT_TYPES)
        .values("occurred_on", "source_type")
        .annotate(total=Sum("amount"))
    ):
        bucket = daily.setdefault(row["occurred_on"], {"received": ZERO, "spent": ZERO})
        if row["source_type"] in CASH_IN_TYPES:
            bucket["received"] += row["total"]
        else:
            bucket["spent"] += -row["total"]

    rows = []
    current = start
    while current <= end:
        bucket = daily.get(current, {"received": ZERO, "spent": ZERO})
        rows.append(
            {
                "date": current,
                "received": bucket["received"],
                "spent": bucket["spent"],
                "difference": bucket["received"] - bucket["spent"],
                "has_activity": bool(bucket["received"] or bucket["spent"]),
            }
        )
        current += timedelta(days=1)

    return {
        "start": start,
        "end": end,
        "received": totals["received"],
        "spent": totals["spent"],
        "difference": totals["difference"],
        "message": describe_difference(totals["received"], totals["spent"]),
        "rows": rows,
        "by_type": by_type,
        "fee_total": -by_type.get(AccountPosting.SourceType.FEE, ZERO),
        "debt_repayment_total": -by_type.get(AccountPosting.SourceType.BORROW_REPAY, ZERO),
        "loan_receipt_total": by_type.get(AccountPosting.SourceType.BORROW_RECEIVE, ZERO),
        "lending_sent_total": -by_type.get(AccountPosting.SourceType.LEND_SEND, ZERO),
        "lending_received_total": by_type.get(AccountPosting.SourceType.LEND_REPAY, ZERO),
        "income_total": by_type.get(AccountPosting.SourceType.INCOME, ZERO),
        "expense_total": -by_type.get(AccountPosting.SourceType.EXPENSE, ZERO),
    }


def weekly_comparison(rows, start, end) -> list:
    """Consecutive 7-day blocks from the period start.

    Sequential blocks (not ISO calendar weeks) so the numbers always add up to
    the selected period's total — "Week 1 vs Week 2" as requested, with no
    partly-outside range silently dropped.
    """
    blocks = []
    index = 1
    cursor = start
    by_date = {row["date"]: row for row in rows}
    while cursor <= end:
        block_end = min(cursor + timedelta(days=6), end)
        received = spent = ZERO
        day = cursor
        while day <= block_end:
            row = by_date.get(day)
            if row:
                received += row["received"]
                spent += row["spent"]
            day += timedelta(days=1)
        blocks.append(
            {
                "index": index,
                "start": cursor,
                "end": block_end,
                "label": f"Week {index} ({cursor.strftime('%d %b')} – {block_end.strftime('%d %b')})",
                "received": received,
                "spent": spent,
                "difference": received - spent,
            }
        )
        cursor = block_end + timedelta(days=1)
        index += 1
    return blocks


def accrual_summary(user, start, end) -> dict:
    """Income *earned* vs expenses *incurred* in the window — the accrual view,
    kept explicitly separate from the cash view above.

    Debt principal repaid is excluded from expenses (it settles a liability,
    it isn't new spending); repayment transaction fees are included once.
    """
    txns = Transaction.objects.filter(user=user, date__gte=start, date__lte=end)
    income = txns.filter(kind=Transaction.Kind.INCOME).aggregate(t=Sum("amount"))["t"] or ZERO
    expense_principal = txns.filter(kind=Transaction.Kind.EXPENSE).aggregate(t=Sum("amount"))["t"] or ZERO
    txn_fees = txns.aggregate(t=Sum("fee_amount"))["t"] or ZERO
    repayment_fees = (
        BorrowingRepayment.objects.filter(user=user, date__gte=start, date__lte=end).aggregate(t=Sum("fee_amount"))["t"]
        or ZERO
    )
    expenses = expense_principal + txn_fees + repayment_fees
    return {
        "income": income,
        "expenses": expenses,
        "difference": income - expenses,
        "transaction_fees": txn_fees,
        "repayment_fees": repayment_fees,
    }


def _in_window(account, day) -> bool:
    if account is None:
        return True
    return account.opening_balance_date is None or day >= account.opening_balance_date


def day_detail(user, day) -> dict:
    """Everything that happened on one day, each source object listed exactly
    once — a transaction's fee is shown *as part of that transaction*, and a
    repayment's fee as part of that repayment, so nothing is counted twice."""
    postings = _windowed_postings(user, day, day)
    totals = _split_totals(postings)

    transactions = [
        txn
        for txn in Transaction.objects.filter(user=user, date=day).select_related("account", "category")
        if _in_window(txn.account, day)
    ]
    for txn in transactions:
        # Decimal-exact "what actually left the account", computed once here
        # instead of with template-side arithmetic.
        txn.cash_out = txn.amount + (txn.fee_amount or ZERO)
    borrowings_received = [
        b
        for b in Borrowing.objects.filter(user=user, date_borrowed=day).select_related("receiving_account")
        if b.debt_type == Borrowing.DebtType.CASH_LOAN and _in_window(b.receiving_account, day)
    ]
    repayments = [
        r
        for r in BorrowingRepayment.objects.filter(user=user, date=day).select_related("borrowing", "paying_account")
        if _in_window(r.paying_account, day)
    ]
    lendings_sent = [
        lend
        for lend in Lending.objects.filter(user=user, date_lent=day).select_related("source_account")
        if _in_window(lend.source_account, day)
    ]
    lending_repayments = [
        r
        for r in LendingRepayment.objects.filter(user=user, date=day).select_related("lending", "receiving_account")
        if _in_window(r.receiving_account, day)
    ]
    transfers = list(Transfer.objects.filter(user=user, occurred_on=day).select_related("from_account", "to_account"))

    by_category = {}
    for txn in transactions:
        if txn.kind != Transaction.Kind.EXPENSE:
            continue
        key = txn.category.name if txn.category else "Uncategorized"
        by_category[key] = by_category.get(key, ZERO) + txn.amount

    by_account = {}
    for row in postings.filter(source_type__in=CASH_OUT_TYPES).values("account__name").annotate(total=Sum("amount")):
        by_account[row["account__name"]] = -row["total"]

    fee_lines = []
    for txn in transactions:
        if txn.fee_amount and txn.fee_amount > 0:
            fee_lines.append({"label": f"Fee: {txn.description or txn.get_kind_display()}", "amount": txn.fee_amount})
    for repayment in repayments:
        if repayment.fee_amount and repayment.fee_amount > 0:
            fee_lines.append(
                {"label": f"Repayment fee to {repayment.borrowing.lender_name}", "amount": repayment.fee_amount}
            )
    for transfer in transfers:
        if transfer.fee_amount and transfer.fee_amount > 0:
            fee_lines.append(
                {"label": f"Transfer fee: {transfer.description or 'transfer'}", "amount": transfer.fee_amount}
            )

    return {
        "date": day,
        "received": totals["received"],
        "spent": totals["spent"],
        "difference": totals["difference"],
        "message": describe_difference(totals["received"], totals["spent"]),
        "transactions": transactions,
        "income_transactions": [t for t in transactions if t.kind == Transaction.Kind.INCOME],
        "expense_transactions": [t for t in transactions if t.kind == Transaction.Kind.EXPENSE],
        "borrowings_received": borrowings_received,
        "repayments": repayments,
        "lendings_sent": lendings_sent,
        "lending_repayments": lending_repayments,
        "transfers": transfers,
        "by_category": sorted(by_category.items(), key=lambda item: item[1], reverse=True),
        "by_account": sorted(by_account.items(), key=lambda item: item[1], reverse=True),
        "fee_lines": fee_lines,
        "fee_total": sum((line["amount"] for line in fee_lines), ZERO),
    }


def analysis(user, period: dict) -> dict:
    """Full page context: cash view + accrual view + weekly + daily rows."""
    cash = period_cash_flow(user, period["start"], period["end"])
    cash["weekly"] = weekly_comparison(cash["rows"], period["start"], period["end"])
    return {
        "period": period,
        "cash": cash,
        "accrual": accrual_summary(user, period["start"], period["end"]),
        "preset_choices": PRESET_CHOICES,
        "show_weekly": len(cash["weekly"]) > 1,
    }

