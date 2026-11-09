from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from apps.accounts.models import AccountPosting, FinancialAccount


def balance_as_of(user, date):
    """Total balance across active accounts, counting only postings dated
    on or before `date` — used to reconstruct a historical day's opening
    and closing position without storing a mutable snapshot."""
    accounts = FinancialAccount.objects.filter(user=user, status=FinancialAccount.Status.ACTIVE)
    opening_total = sum((a.opening_balance for a in accounts), Decimal("0.00"))
    posted = (
        AccountPosting.objects.filter(user=user, account__in=accounts, occurred_on__lte=date).aggregate(
            total=Sum("amount")
        )["total"]
        or Decimal("0.00")
    )
    return opening_total + posted


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
    return {
        "opening_balance": balance_as_of(user, date - timedelta(days=1)),
        "closing_balance": balance_as_of(user, date),
        "received": received,
        "spent": spent,
        "net": received - spent,
        "by_type": totals,
    }
