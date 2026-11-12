"""Read-only opening-balance reconciliation (Phase 13, improvement 7).

The recurring bug this addresses: an account was created with the owner's real
available cash as its `opening_balance`, and an old cash loan was recorded
later with its true (much earlier) borrowing date. For an account with no
`opening_balance_date`, every posting ever recorded is summed into the balance,
so that historical loan inflates today's cash a second time.

Nothing in this module touches money on its own:

* `account_reconciliation()` / `reconciliation_preview()` are strictly
  read-only and exist so the owner can see exactly what is recorded, what the
  current balance is, and what applying a snapshot date *would* do.
* `apply_opening_snapshot_date()` changes exactly one nullable metadata column
  (`opening_balance_date`) and writes an audit event. It never edits an
  opening balance, a posting, a loan or a repayment — a naive automatic
  correction could subtract the same historical loan twice on accounts whose
  balance was already hand-adjusted.

Snapshot semantics (one definition, used everywhere): `opening_balance` is the
money available at the **start** of `opening_balance_date`. Postings dated on
or after that date are added on top; postings dated before it are historical
record only. That is what makes same-day transactions count exactly once.
"""

from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.audit import log_audit_event
from apps.debts.models import Borrowing, BorrowingRepayment

from .models import FinancialAccount

ZERO = Decimal("0.00")


def _posting_sum(postings) -> Decimal:
    return postings.aggregate(total=Sum("amount"))["total"] or ZERO


def snapshot_balance(account: FinancialAccount, cutoff) -> Decimal:
    """What this account's balance *would* be with `cutoff` as its snapshot
    date. Pure calculation — identical logic to `services.account_balance`,
    parameterised so we can preview a date the account doesn't have yet."""
    postings = account.postings.all()
    if cutoff is not None:
        postings = postings.filter(occurred_on__gte=cutoff)
    return account.opening_balance + _posting_sum(postings)


def _created_date(account: FinancialAccount):
    return timezone.localtime(account.created_at).date()



def account_reconciliation(account: FinancialAccount) -> dict:
    """Full, read-only picture of one account for the review screen."""
    cutoff = account.opening_balance_date
    all_postings = account.postings.all()
    if cutoff is not None:
        before = all_postings.filter(occurred_on__lt=cutoff)
        after = all_postings.filter(occurred_on__gte=cutoff)
    else:
        before = all_postings
        after = all_postings.none()

    current_balance = snapshot_balance(account, cutoff)
    created = _created_date(account)
    # Without a snapshot date, the app has no idea when the opening balance was
    # true; the account's own creation date is the only evidenced reference
    # point, so that is what the preview compares against (it is shown as a
    # question, never applied silently).
    reference_date = cutoff if cutoff is not None else created

    historical_cash_loans = list(
        Borrowing.objects.filter(
            user=account.user,
            receiving_account=account,
            debt_type=Borrowing.DebtType.CASH_LOAN,
            date_borrowed__lt=reference_date,
        )
    )
    historical_repayments = list(
        BorrowingRepayment.objects.filter(user=account.user, paying_account=account, date__lt=reference_date)
    )

    candidates = []
    if cutoff is None:
        seen = set()
        earliest_posting = all_postings.order_by("occurred_on").values_list("occurred_on", flat=True).first()
        for candidate in (created, earliest_posting):
            if candidate is None or candidate in seen:
                continue
            seen.add(candidate)
            projected = snapshot_balance(account, candidate)
            candidates.append({"date": candidate, "balance": projected, "difference": projected - current_balance})

    ambiguity = []
    if cutoff is None:
        if before.exists():
            ambiguity.append(
                f"This account has no snapshot date, so all {before.count()} of its postings are added to the "
                "opening balance — including entries dated before the account existed here. If your opening "
                "balance already reflected them, they are currently counted twice."
            )
        else:
            ambiguity.append(
                "This account has no snapshot date. Nothing predates its creation, so its balance is almost "
                "certainly right today — but a later entry with a backdated date would still distort it."
            )
        if historical_cash_loans:
            ambiguity.append(
                "A historical cash loan dated before this account's creation date is currently adding to its "
                "balance. If that money was already spent before you started tracking, the balance is overstated."
            )
    else:
        ambiguity.append(
            f"Snapshot date is set to {cutoff}: postings dated before it ({before.count()}) are excluded from the "
            "balance, and the opening balance is taken as true at the start of that day."
        )

    return {
        "account": account,
        "opening_balance": account.opening_balance,
        "opening_balance_date": cutoff,
        "created_date": created,
        "current_balance": current_balance,
        "before_cutoff_count": before.count(),
        "before_cutoff_sum": _posting_sum(before),
        "after_cutoff_count": after.count(),
        "after_cutoff_sum": _posting_sum(after),
        "historical_cash_loans": historical_cash_loans,
        "historical_repayments": historical_repayments,
        "candidates": candidates,
        "needs_review": cutoff is None,
        # Deliberately equal to the current balance: this screen proposes no
        # automatic money change. Only the owner can decide a snapshot date.
        "proposed_balance": current_balance,
        "proposed_difference": ZERO,
        "explanation": (
            "No correction is proposed automatically. Set a snapshot date only if you are sure the opening "
            "balance you entered reflects everything dated before it."
            if cutoff is None
            else "This account already has a snapshot date, so historical entries are not double-counted."
        ),
        "ambiguity": ambiguity,
    }


def reconciliation_preview(user) -> dict:
    accounts = list(FinancialAccount.objects.filter(user=user).order_by("status", "name"))
    rows = [account_reconciliation(account) for account in accounts]
    return {
        "accounts": rows,
        "needs_review": [row for row in rows if row["needs_review"]],
        "needs_review_count": sum(1 for row in rows if row["needs_review"]),
        "ok_count": sum(1 for row in rows if not row["needs_review"]),
        "historical_cash_loan_total": sum(
            (loan.outstanding() for row in rows for loan in row["historical_cash_loans"]), ZERO
        ),
    }


@db_transaction.atomic
def apply_opening_snapshot_date(*, account: FinancialAccount, new_date, actor=None) -> dict:
    """Apply the owner's explicitly chosen snapshot date.

    Transactional, audited and reversible: passing `new_date=None` restores the
    previous "no cutoff" behavior. Nothing except the snapshot date is written.
    """
    previous_date = account.opening_balance_date
    before = snapshot_balance(account, previous_date)
    account.opening_balance_date = new_date
    account.save(update_fields=["opening_balance_date"])
    after = snapshot_balance(account, new_date)

    result = {
        "account": account,
        "previous_date": previous_date,
        "new_date": new_date,
        "balance_before": before,
        "balance_after": after,
        "difference": after - before,
    }
    log_audit_event(
        actor,
        "update",
        obj=account,
        description=(
            f"Opening snapshot date for {account.name} set to {new_date}"
            if new_date
            else f"Opening snapshot date cleared for {account.name}"
        ),
        metadata={
            "previous_date": previous_date.isoformat() if previous_date else None,
            "new_date": new_date.isoformat() if new_date else None,
            "opening_balance": str(account.opening_balance),
            "balance_before": str(before),
            "balance_after": str(after),
        },
    )
    return result
