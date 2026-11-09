from django.db import transaction as db_transaction

from apps.accounts.models import AccountPosting
from apps.accounts.services import delete_postings, sync_postings

from .models import Borrowing, BorrowingRepayment, Lending, LendingRepayment


@db_transaction.atomic
def create_borrowing(*, user, **fields) -> Borrowing:
    borrowing = Borrowing.objects.create(user=user, **fields)
    sync_postings(
        borrowing,
        [
            dict(
                account=borrowing.receiving_account,
                amount=borrowing.amount,
                source_type=AccountPosting.SourceType.BORROW_RECEIVE,
                occurred_on=borrowing.date_borrowed,
                description=f"Borrowed from {borrowing.lender_name}",
            )
        ],
        user=user,
    )
    return borrowing


@db_transaction.atomic
def delete_borrowing(borrowing: Borrowing):
    for repayment in borrowing.repayments.all():
        delete_postings(repayment)
    delete_postings(borrowing)
    borrowing.delete()


@db_transaction.atomic
def record_borrowing_repayment(*, user, borrowing: Borrowing, **fields) -> BorrowingRepayment:
    if fields.get("amount") and fields["amount"] > borrowing.outstanding():
        raise ValueError(
            f"Repayment of {fields['amount']} exceeds the {borrowing.outstanding()} still outstanding."
        )
    repayment = BorrowingRepayment.objects.create(user=user, borrowing=borrowing, **fields)
    sync_postings(
        repayment,
        [
            dict(
                account=repayment.paying_account,
                amount=-repayment.amount,
                source_type=AccountPosting.SourceType.BORROW_REPAY,
                occurred_on=repayment.date,
                description=f"Repayment to {borrowing.lender_name}",
            )
        ],
        user=user,
    )
    _refresh_borrowing_status(borrowing)
    return repayment


@db_transaction.atomic
def delete_borrowing_repayment(repayment: BorrowingRepayment):
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
