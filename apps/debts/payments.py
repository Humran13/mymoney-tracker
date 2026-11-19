"""One payment covering several of one person's outstanding transactions.

Reuses the per-record services (`record_lending_repayment` /
`record_borrowing_repayment`), which write the individual repayment rows and
their account postings through `sync_postings`. This module only decides the
allocation (oldest outstanding first) and wraps everything in one DB
transaction guarded by an idempotency key, so the whole payment lands exactly
once or not at all. No second accounting mechanism is introduced.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction as db_transaction

from apps.core.models import IdempotencyKey

from . import services
from .models import Borrowing, Lending
from .settlements import BORROWER, LENDER, ZERO, person_key

CENT = Decimal("0.01")


class DuplicatePayment(ValueError):
    pass


def _q(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def already_recorded(user, token) -> bool:
    return IdempotencyKey.objects.filter(user=user, key=token).exists()


def person_records(user, side, name, contact="", *, lock=False) -> list:
    """Active records with a balance for one person on one side, oldest first.
    `outstanding_now` is attached to each. Matching is by the same
    normalized (name, contact) key the dashboard grouping uses."""
    if side == LENDER:
        qs = Borrowing.objects.filter(user=user, status=Borrowing.Status.ACTIVE).order_by("date_borrowed", "id")
        name_of = lambda r: r.lender_name  # noqa: E731
    elif side == BORROWER:
        qs = Lending.objects.filter(user=user, status=Lending.Status.ACTIVE).order_by("date_lent", "id")
        name_of = lambda r: r.borrower_name  # noqa: E731
    else:
        raise ValueError("Unknown side.")
    if lock:
        qs = qs.select_for_update()
    wanted = person_key(name, contact)
    records = [r for r in qs if person_key(name_of(r), r.contact) == wanted]
    for r in records:
        r.outstanding_now = r.outstanding()
    return [r for r in records if r.outstanding_now > 0]


def allocate(records, principal) -> list:
    """Oldest outstanding first. Never allocates more than a record's balance."""
    remaining, result = _q(principal), []
    for record in records:
        if remaining <= 0:
            break
        part = min(record.outstanding_now, remaining)
        if part > 0:
            result.append((record, part))
            remaining -= part
    return result


def total_outstanding(records) -> Decimal:
    return sum((r.outstanding_now for r in records), ZERO)


@db_transaction.atomic
def record_person_payment(
    *, user, side, name, contact="", principal, interest=0, fee=0, date, account, notes="", token
) -> list:
    """Record one real payment against all of a person's outstanding records.

    Returns [(record, repayment), ...]. Raises DuplicatePayment for a repeated
    token and ValueError for an invalid amount; in both cases nothing is saved.
    `interest` (money received on top of principal, borrower side only) and
    `fee` (channel charge, lender side only) are attached once, to the first
    allocation, so they are posted once for the whole payment.
    """
    principal, interest, fee = _q(principal), _q(interest), _q(fee)
    if account.user_id != user.pk:
        raise ValueError("Unknown account.")
    try:
        with db_transaction.atomic():
            IdempotencyKey.objects.create(user=user, key=token, scope=f"{side}-payment")
    except IntegrityError:
        raise DuplicatePayment("This payment was already recorded.") from None

    records = person_records(user, side, name, contact, lock=True)
    if not records:
        raise ValueError("This person has nothing outstanding.")
    if min(principal, interest, fee) < 0:
        raise ValueError("Amounts cannot be negative.")
    if side == LENDER and interest:
        raise ValueError("Interest does not apply to a debt you are repaying.")
    if side == BORROWER and fee:
        raise ValueError("A transaction fee does not apply to money you receive.")
    if principal <= 0 and interest <= 0:
        raise ValueError("Enter an amount received.")
    if side == LENDER and principal <= 0:
        raise ValueError("Payment amount must be greater than zero.")
    outstanding = total_outstanding(records)
    if principal > outstanding:
        raise ValueError(f"That is more than the {outstanding:,.2f} still outstanding.")

    plan = allocate(records, principal)
    if not plan:  # interest-only payment: attach to the oldest record
        plan = [(records[0], ZERO)]
    results = []
    for index, (record, part) in enumerate(plan):
        first = index == 0
        if side == BORROWER:
            repayment = services.record_lending_repayment(
                user=user, lending=record, principal_amount=part,
                interest_amount=interest if first else ZERO,
                date=date, receiving_account=account, notes=notes,
            )
        else:
            repayment = services.record_borrowing_repayment(
                user=user, borrowing=record, amount=part, fee_amount=fee if first else ZERO,
                date=date, paying_account=account, notes=notes,
            )
        results.append((record, repayment))
    return results
