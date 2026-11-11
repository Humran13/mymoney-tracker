from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from apps.accounts.models import AccountPosting, FinancialAccount


def balance_as_of(user, date):
    """Total balance across active accounts, counting only postings dated
    on or before `date` and respecting each account's own opening-balance
    cutoff — used to reconstruct a historical day's opening and closing
    position without storing a mutable snapshot.

    Returns None if `date` falls before any active account's
    opening_balance_date: the opening_balance figure is only known to be
    true as of that cutoff, so any total computed for an earlier date would
    either double-count pre-cutoff postings already baked into
    opening_balance, or invent a number that was never actually recorded.
    Callers must show "balance unavailable" rather than a 0 or a guess."""
    accounts = list(FinancialAccount.objects.filter(user=user, status=FinancialAccount.Status.ACTIVE))
    if any(a.opening_balance_date and date < a.opening_balance_date for a in accounts):
        return None
    total = Decimal("0.00")
    for account in accounts:
        postings = AccountPosting.objects.filter(user=user, account=account, occurred_on__lte=date)
        if account.opening_balance_date is not None:
            postings = postings.filter(occurred_on__gte=account.opening_balance_date)
        posted = postings.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        total += account.opening_balance + posted
    return total


def day_breakdown(user, date):
    postings = AccountPosting.objects.filter(user=user, occurred_on=date)
    totals = dict(postings.values_list("source_type").annotate(total=Sum("amount")))
    received = sum(
        (v for k, v in totals.items() if k in (AccountPosting.SourceType.INCOME, AccountPosting.SourceType.BORROW_RECEIVE, AccountPosting.SourceType.TRANSFER_IN, AccountPosting.SourceType.LEND_REPAY)),
        Decimal("0.00"),
    )
    spent = sum(
        (-v for k, v in totals.items() if k in (AccountPosting.SourceType.EXPENSE, AccountPosting.SourceType.FEE, AccountPosting.SourceType.LEND_SEND, AccountPosting.SourceType.BORROW_REPAY, AccountPosting.SourceType.TRANSFER_OUT, AccountPosting.SourceType.BILL_PAYMENT)),
        Decimal("0.00"),
    )
    opening_balance = balance_as_of(user, date - timedelta(days=1))
    closing_balance = balance_as_of(user, date)
    return {
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "balance_unavailable": opening_balance is None or closing_balance is None,
        "received": received,
        "spent": spent,
        "net": received - spent,
        "by_type": totals,
    }
