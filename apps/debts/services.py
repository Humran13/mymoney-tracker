from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Sum

from apps.accounts.models import AccountPosting
from apps.accounts.services import delete_postings, sync_postings

from .models import Borrowing, BorrowingRepayment, Lending, LendingRepayment


def _borrowing_postings(borrowing: Borrowing) -> list:
    """Only a CASH_LOAN ever creates a real cash-deposit posting. Goods/
    services/other credit obligations increase what's owed (via
    outstanding()/total_owed(), which read the model fields directly, not
    postings) without ever touching an account balance.

    The receipt is always dated `date_borrowed` — never "today" — so a loan
    remembered and entered months later still lands on its real date. When
    that date precedes the receiving account's opening-balance snapshot
    (`FinancialAccount.opening_balance_date`), the posting is kept as an
    auditable record of the event but `account_balance()` excludes it: the
    money is already part of the opening balance, so adding it again would
    invent cash the user never had. See
    `apps.accounts.reconciliation` and Phase 13 handoff notes.
    """
    if borrowing.debt_type != Borrowing.DebtType.CASH_LOAN:
        return []
    return [
        dict(
            account=borrowing.receiving_account,
            amount=borrowing.amount,
            source_type=AccountPosting.SourceType.BORROW_RECEIVE,
            occurred_on=borrowing.date_borrowed,
            description=f"Borrowed from {borrowing.lender_name}",
        )
    ]


@db_transaction.atomic
def create_borrowing(*, user, **fields) -> Borrowing:
    borrowing = Borrowing.objects.create(user=user, **fields)
    sync_postings(borrowing, _borrowing_postings(borrowing), user=user)
    return borrowing


@db_transaction.atomic
def reclassify_borrowing(*, borrowing: Borrowing, debt_type, receiving_account=None) -> Borrowing:
    """Change a debt's type (e.g. a cash loan that was actually goods
    received on credit). Preserves the record, its creditor, amount, dates,
    and every repayment — only the BORROW_RECEIVE posting is added/removed
    through the normal sync_postings path, and account balances recompute
    from that automatically."""
    borrowing.debt_type = debt_type
    borrowing.receiving_account = receiving_account if debt_type == Borrowing.DebtType.CASH_LOAN else None
    borrowing.full_clean()
    borrowing.save(update_fields=["debt_type", "receiving_account"])
    sync_postings(borrowing, _borrowing_postings(borrowing), user=borrowing.user)
    return borrowing


def preview_reclassify_borrowing(borrowing: Borrowing, debt_type, receiving_account=None) -> dict:
    """Read-only: what balance changes reclassify_borrowing() would cause,
    computed without writing anything, so the UI can show it before the
    user confirms."""
    from decimal import Decimal

    changes = {}
    if borrowing.debt_type == Borrowing.DebtType.CASH_LOAN and borrowing.receiving_account_id:
        changes[borrowing.receiving_account] = changes.get(borrowing.receiving_account, Decimal("0.00")) - borrowing.amount
    if debt_type == Borrowing.DebtType.CASH_LOAN and receiving_account is not None:
        changes[receiving_account] = changes.get(receiving_account, Decimal("0.00")) + borrowing.amount
    return changes


@db_transaction.atomic
def delete_borrowing(borrowing: Borrowing):
    for repayment in borrowing.repayments.all():
        delete_postings(repayment)
    delete_postings(borrowing)
    borrowing.delete()


def _available_to_repay(borrowing: Borrowing, *, exclude_repayment=None) -> Decimal:
    """How much principal is still repayable, ignoring `exclude_repayment`
    itself. Needed when *editing* an existing repayment: its own amount has
    to be added back before checking a new figure, otherwise editing
    100,000 -> 150,000 on a 200,000 loan would look like an overpayment."""
    repaid = borrowing.repayments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    if exclude_repayment is not None and exclude_repayment.pk:
        repaid -= exclude_repayment.amount
    return borrowing.total_owed() - repaid


def _repayment_postings(repayment: BorrowingRepayment) -> list:
    """Principal and fee are deliberately two separate postings.

    - BORROW_REPAY reduces the paying account by the principal only, and is
      what debt reporting/cash-out reporting treats as a debt repayment.
    - FEE reduces the paying account by the transaction charge and is a real
      expense, but it must never be read as reducing the debt principal.

    Because both live on the same source object, `sync_postings` replaces
    them together on edit and `delete_postings` removes them together on
    delete — there is no path that can leave a fee posting orphaned or
    double-posted.
    """
    specs = [
        dict(
            account=repayment.paying_account,
            amount=-repayment.amount,
            source_type=AccountPosting.SourceType.BORROW_REPAY,
            occurred_on=repayment.date,
            description=f"Repayment to {repayment.borrowing.lender_name}",
        )
    ]
    if repayment.fee_amount and repayment.fee_amount > 0:
        specs.append(
            dict(
                account=repayment.paying_account,
                amount=-repayment.fee_amount,
                source_type=AccountPosting.SourceType.FEE,
                occurred_on=repayment.date,
                description=f"Repayment transaction fee ({repayment.paying_account.name})",
            )
        )
    return specs


def _validate_repayment_amounts(*, amount, fee_amount):
    fee = fee_amount or Decimal("0.00")
    if fee < 0:
        raise ValueError("Transaction fee cannot be negative.")
    if amount is None or amount <= 0:
        raise ValueError("Repayment amount must be greater than zero.")
    return fee


@db_transaction.atomic
def record_borrowing_repayment(*, user, borrowing: Borrowing, **fields) -> BorrowingRepayment:
    available = _available_to_repay(borrowing)
    fee = _validate_repayment_amounts(amount=fields.get("amount"), fee_amount=fields.get("fee_amount"))
    if fields["amount"] > available:
        raise ValueError(f"Repayment of {fields['amount']} exceeds the {available} still outstanding.")
    fields["fee_amount"] = fee
    repayment = BorrowingRepayment.objects.create(user=user, borrowing=borrowing, **fields)
    sync_postings(repayment, _repayment_postings(repayment), user=user)
    _refresh_borrowing_status(borrowing)
    return repayment


@db_transaction.atomic
def update_borrowing_repayment(repayment: BorrowingRepayment, **fields) -> BorrowingRepayment:
    """Edit an existing repayment (amount, date, account, fee). Postings are
    fully replaced, so changing or clearing the fee removes exactly the old
    fee posting and creates at most one new one."""
    borrowing = repayment.borrowing
    available = _available_to_repay(borrowing, exclude_repayment=repayment)
    amount = fields.get("amount", repayment.amount)
    fee = _validate_repayment_amounts(amount=amount, fee_amount=fields.get("fee_amount", repayment.fee_amount))
    if amount > available:
        raise ValueError(f"Repayment of {amount} exceeds the {available} still outstanding.")
    for key, value in fields.items():
        setattr(repayment, key, value)
    repayment.fee_amount = fee
    repayment.full_clean(exclude=["user"])
    repayment.save()
    sync_postings(repayment, _repayment_postings(repayment), user=repayment.user)
    _refresh_borrowing_status(borrowing)
    return repayment


@db_transaction.atomic
def delete_borrowing_repayment(repayment: BorrowingRepayment):
    """Removes the repayment *and* its fee posting in one transaction."""
    borrowing = repayment.borrowing
    delete_postings(repayment)
    repayment.delete()
    _refresh_borrowing_status(borrowing)


def _refresh_borrowing_status(borrowing: Borrowing):
    borrowing.status = Borrowing.Status.SETTLED if borrowing.outstanding() <= 0 else Borrowing.Status.ACTIVE
    borrowing.save(update_fields=["status"])


@db_transaction.atomic
def create_lending(*, user, **fields) -> Lending:
    lending = Lending.objects.create(user=user, **fields)
    sync_postings(
        lending,
        [
            dict(
                account=lending.source_account,
                amount=-lending.amount,
                source_type=AccountPosting.SourceType.LEND_SEND,
                occurred_on=lending.date_lent,
                description=f"Lent to {lending.borrower_name}",
            )
        ],
        user=user,
    )
    return lending


@db_transaction.atomic
def delete_lending(lending: Lending):
    for repayment in lending.repayments.all():
        delete_postings(repayment)
    delete_postings(lending)
    lending.delete()


@db_transaction.atomic
def record_lending_repayment(*, user, lending: Lending, **fields) -> LendingRepayment:
    principal = fields.get("principal_amount") or 0
    if principal and principal > lending.outstanding():
        raise ValueError(
            f"Principal repayment of {principal} exceeds the {lending.outstanding()} still owed."
        )
    repayment = LendingRepayment.objects.create(user=user, lending=lending, **fields)
    specs = []
    if repayment.principal_amount:
        specs.append(
            dict(
                account=repayment.receiving_account,
                amount=repayment.principal_amount,
                source_type=AccountPosting.SourceType.LEND_REPAY,
                occurred_on=repayment.date,
                description=f"Principal repaid by {lending.borrower_name}",
            )
        )
    if repayment.interest_amount:
        specs.append(
            dict(
                account=repayment.receiving_account,
                amount=repayment.interest_amount,
                source_type=AccountPosting.SourceType.INCOME,
                occurred_on=repayment.date,
                description=f"Interest from {lending.borrower_name}",
            )
        )
    sync_postings(repayment, specs, user=user)
    _refresh_lending_status(lending)
    return repayment


@db_transaction.atomic
def delete_lending_repayment(repayment: LendingRepayment):
    lending = repayment.lending
    delete_postings(repayment)
    repayment.delete()
    _refresh_lending_status(lending)


def _refresh_lending_status(lending: Lending):
    lending.status = Lending.Status.SETTLED if lending.outstanding() <= 0 else Lending.Status.ACTIVE
    lending.save(update_fields=["status"])
