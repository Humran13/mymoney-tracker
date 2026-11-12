"""Starting-balance transparency screen.

An account's balance is `opening_balance + postings on/after its starting
date`. The starting date is the user's explicitly declared
`opening_balance_date`, or — automatically — the day the account was added
(see `apps/accounts.models.balance_start_date`). Nothing here is required to
make balances correct: the calculation is already automatic.

Nothing in this module touches money:

* `account_reconciliation()` / `reconciliation_preview()` are strictly
  read-only. They show where the starting point came from, what falls before
  it, and what changing it *would* do.
* `apply_opening_snapshot_date()` changes exactly one nullable metadata column
  (`opening_balance_date`) and writes an audit event. It never edits an opening
  balance, a posting, a loan or a repayment — a naive automatic correction could
  subtract the same historical loan twice on accounts whose balance was already
  hand-adjusted.

Semantics (one definition, used by every calculation): `opening_balance` is the
money available at the **start** of the starting date. Postings dated on or
after it are added on top; postings dated before it are history only.
"""

from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.audit import log_audit_event
from apps.core.models import AuditEvent
from apps.debts.models import Borrowing, BorrowingRepayment

from .models import FinancialAccount, balance_start_date

ZERO = Decimal("0.00")


def _posting_sum(postings) -> Decimal:
    return postings.aggregate(total=Sum("amount"))["total"] or ZERO


def snapshot_balance(account: FinancialAccount, cutoff) -> Decimal:
    """What this account's balance *would* be with `cutoff` as its starting
    date. Pure calculation — identical logic to `services.account_balance`,
    parameterised so the screen can preview a date the account doesn't have yet.
    """
    postings = account.postings.all()
    if cutoff is not None:
        postings = postings.filter(occurred_on__gte=cutoff)
    return account.opening_balance + _posting_sum(postings)



def _created_date(account: FinancialAccount):
    created = account.created_at
    if created is None:
        return None
    return timezone.localtime(created).date() if timezone.is_aware(created) else created.date()


def account_reconciliation(account: FinancialAccount) -> dict:
    """Full, read-only picture of one account's starting point."""
    start = balance_start_date(account)
    declared = account.opening_balance_date
    all_postings = account.postings.all()
    before = all_postings.filter(occurred_on__lt=start)
    after = all_postings.filter(occurred_on__gte=start)

    current_balance = snapshot_balance(account, start)
    created = _created_date(account)

    historical_cash_loans = list(
        Borrowing.objects.filter(
            user=account.user,
            receiving_account=account,
            debt_type=Borrowing.DebtType.CASH_LOAN,
            date_borrowed__lt=start,
        )
    )
    historical_repayments = list(
        BorrowingRepayment.objects.filter(user=account.user, paying_account=account, date__lt=start)
    )

    notes = []
    if declared is not None:
        notes.append(
            f"Your starting date is {start} (the date you set). Entries dated before it "
            f"({before.count()}) are kept as history and are not added to your balance."
        )
    else:
        notes.append(
            f"Your starting date is {start} — the day this account was added and this balance was entered. "
            f"Entries dated before it ({before.count()}) are kept as history and are not added to your balance."
        )
    if before.exists():
        notes.append(
            "That is deliberate: your starting balance already reflects everything that had happened by then, "
            "so adding those entries again would show money you do not actually have."
        )

    ambiguity = []
    if before.exists() and account.opening_balance == 0:
        ambiguity.append(
            "Your starting balance is 0 and some entries are dated earlier, so they are not counted. If that "
            "history should count towards this account, set a starting date before "
            f"{start} — nothing is changed unless you do."
        )
    edits = (
        AuditEvent.objects.filter(
            object_type="FinancialAccount", object_id=str(account.pk), action="update"
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)[:3]
    )
    edit_dates = sorted({timezone.localtime(dt).date() for dt in edits}, reverse=True)
    if edit_dates:
        ambiguity.append(
            "This account has been edited since it was created ("
            + ", ".join(str(d) for d in edit_dates)
            + "). If its starting balance was adjusted to offset an earlier miscalculation, please check "
            "the figure below — no automatic correction has been made."
        )
    if historical_cash_loans:
        ambiguity.append(
            "A loan recorded here was actually received before this account's starting date. That loan still "
            "increases what you owe, but it does not increase this account's balance — the money was already "
            "spent before you started tracking."
        )

    return {
        "account": account,
        "opening_balance": account.opening_balance,
        "opening_balance_date": declared,
        "start_date": start,
        "start_is_declared": declared is not None,
        "created_date": created,
        "current_balance": current_balance,
        "before_cutoff_count": before.count(),
        "before_cutoff_sum": _posting_sum(before),
        "after_cutoff_count": after.count(),
        "after_cutoff_sum": _posting_sum(after),
        "historical_cash_loans": historical_cash_loans,
        "historical_repayments": historical_repayments,
        # Kept for the template's badge/tone only; no action is required.
        "has_history_before_start": before.exists(),
        # Deliberately equal to the current balance: this screen proposes no
        # automatic money change.
        "proposed_balance": current_balance,
        "proposed_difference": ZERO,
        "explanation": (
            "Nothing needs correcting here. Change the starting date only if you know this balance describes "
            "a different day."
        ),
        "notes": notes,
        "ambiguity": ambiguity,
    }


def reconciliation_preview(user) -> dict:
    accounts = list(FinancialAccount.objects.filter(user=user).order_by("status", "name"))
    rows = [account_reconciliation(account) for account in accounts]
    with_history = [row for row in rows if row["has_history_before_start"]]
    return {
        "accounts": rows,
        "with_history_count": len(with_history),
        "with_history": with_history,
        "declared_count": sum(1 for row in rows if row["start_is_declared"]),
        "derived_count": sum(1 for row in rows if not row["start_is_declared"]),
        "historical_cash_loan_total": sum(
            (loan.outstanding() for row in rows for loan in row["historical_cash_loans"]), ZERO
        ),
    }


@db_transaction.atomic
def apply_opening_snapshot_date(*, account: FinancialAccount, new_date, actor=None) -> dict:
    """Apply an explicitly chosen starting date (an override, not a correction).

    Transactional, audited and reversible: passing `new_date=None` falls back to
    the automatic date. Nothing except that one column is written.
    """
    previous_date = account.opening_balance_date
    # "Before" must be the balance the user actually sees, which for an account
    # with no declared date is computed from its automatic start — not from
    # "no cutoff at all" (that old value is what this screen used to overstate).
    before = snapshot_balance(account, balance_start_date(account))
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
