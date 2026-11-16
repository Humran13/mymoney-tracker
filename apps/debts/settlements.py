"""Per-person grouping of debts/loans and noncash offsets between them.

A settlement writes only DebtSettlement/DebtSettlementLine rows. It has no
account foreign key and never calls sync_postings, so no account balance can
move as a result of one.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction as db_transaction

from .models import Borrowing, DebtSettlement, DebtSettlementLine, Lending
from .services import _refresh_borrowing_status, _refresh_lending_status

ZERO = Decimal("0.00")
_KEY_SEP = "\x1f"


def clean_name(value: str) -> str:
    """Collapse runs of whitespace and trim, keeping the user's capitalization."""
    return " ".join((value or "").split())


def person_key(name: str, contact: str = "") -> str:
    """Identity of a person: name ignoring case and extra whitespace, plus the
    optional contact. Records merge only when both match, so two different
    people sharing a name stay apart when their contacts differ."""
    return clean_name(name).casefold() + _KEY_SEP + clean_name(contact).casefold()


def _group(records, name_attr, date_attr):
    """One entry per person; `records` must be newest-first so the display name
    comes from the most recent record."""
    groups = {}
    for record in records:
        name = getattr(record, name_attr)
        key = person_key(name, record.contact)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "key": key,
                "name": clean_name(name),
                "contact": clean_name(record.contact),
                "items": [],
                "total": ZERO,
                "offsettable": ZERO,
            }
        group["items"].append(record)
        group["total"] += record.outstanding_now
    for group in groups.values():
        group["items"].sort(key=lambda r: (getattr(r, date_attr), r.pk))
    return sorted(groups.values(), key=lambda g: (-g["total"], g["name"].casefold()))


def _active_with_balance(model, user, related):
    records = list(model.objects.filter(user=user, status="active").select_related(related))
    for record in records:
        record.outstanding_now = record.outstanding()
    return [r for r in records if r.outstanding_now > 0]


def debt_positions(user) -> dict:
    """Active debts ("lenders") and loans ("borrowers") grouped one entry per
    person, with `offsettable` set on people who appear on both sides."""
    lenders = _group(_active_with_balance(Borrowing, user, "receiving_account"), "lender_name", "date_borrowed")
    borrowers = _group(_active_with_balance(Lending, user, "source_account"), "borrower_name", "date_lent")
    lender_by_key = {g["key"]: g for g in lenders}
    for group in borrowers:
        match = lender_by_key.get(group["key"])
        if match:
            group["offsettable"] = match["offsettable"] = min(group["total"], match["total"])
    return {"lenders": lenders, "borrowers": borrowers}


def _candidates(user, name, contact, *, lock):
    """Active debts and loans with a balance for one person, oldest first."""
    key = person_key(name, contact)
    b_qs = Borrowing.objects.filter(user=user, status=Borrowing.Status.ACTIVE).order_by("date_borrowed", "id")
    l_qs = Lending.objects.filter(user=user, status=Lending.Status.ACTIVE).order_by("date_lent", "id")
    if lock:
        b_qs, l_qs = b_qs.select_for_update(), l_qs.select_for_update()
    borrowings = [b for b in b_qs if person_key(b.lender_name, b.contact) == key]
    lendings = [x for x in l_qs if person_key(x.borrower_name, x.contact) == key]
    for record in (*borrowings, *lendings):
        record.outstanding_now = record.outstanding()
    return [b for b in borrowings if b.outstanding_now > 0], [x for x in lendings if x.outstanding_now > 0]


def settlement_summary(user, name, contact="") -> dict:
    borrowings, lendings = _candidates(user, name, contact, lock=False)
    owed_by_me = sum((b.outstanding_now for b in borrowings), ZERO)
    owed_to_me = sum((x.outstanding_now for x in lendings), ZERO)
    return {
        "borrowings": borrowings,
        "lendings": lendings,
        "owed_by_me": owed_by_me,
        "owed_to_me": owed_to_me,
        "maximum": min(owed_by_me, owed_to_me),
        "display_name": clean_name(borrowings[-1].lender_name) if borrowings else clean_name(name),
    }


class DuplicateSettlement(ValueError):
    def __init__(self, settlement):
        super().__init__("This settlement was already recorded.")
        self.settlement = settlement


def _allocate(records, amount):
    remaining = amount
    for record in records:
        if remaining <= 0:
            return
        part = min(record.outstanding_now, remaining)
        if part > 0:
            yield record, part
            remaining -= part


@db_transaction.atomic
def settle_with_person(*, user, name, contact="", amount, date, notes="", token) -> DebtSettlement:
    """Offset up to min(owed by me, owed to me) against one person, oldest
    records first on each side. Rows are locked and re-read inside the
    transaction so the amount is checked against live balances, and `token`
    makes a repeated submit return DuplicateSettlement instead of posting twice."""
    duplicate = DebtSettlement.objects.filter(user=user, idempotency_key=token).first()
    if duplicate:
        raise DuplicateSettlement(duplicate)

    borrowings, lendings = _candidates(user, name, contact, lock=True)
    if not borrowings or not lendings:
        raise ValueError("There is nothing to offset: this person must both owe you and be owed by you.")
    amount = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValueError("Settlement amount must be greater than zero.")
    maximum = min(
        sum((b.outstanding_now for b in borrowings), ZERO),
        sum((x.outstanding_now for x in lendings), ZERO),
    )
    if amount > maximum:
        raise ValueError(f"The most that can be offset right now is {maximum:,.2f}.")

    try:
        with db_transaction.atomic():
            settlement = DebtSettlement.objects.create(
                user=user,
                person_name=clean_name(borrowings[-1].lender_name),
                person_key=person_key(name, contact),
                amount=amount,
                date=date,
                notes=notes,
                idempotency_key=token,
            )
    except IntegrityError:
        raise DuplicateSettlement(DebtSettlement.objects.get(user=user, idempotency_key=token)) from None

    for borrowing, part in _allocate(borrowings, amount):
        DebtSettlementLine.objects.create(user=user, settlement=settlement, borrowing=borrowing, amount=part)
        _refresh_borrowing_status(borrowing)
    for lending, part in _allocate(lendings, amount):
        DebtSettlementLine.objects.create(user=user, settlement=settlement, lending=lending, amount=part)
        _refresh_lending_status(lending)
    return settlement
