"""Per-person grouping of debts/loans, identity matching, and noncash offsets.

A settlement writes only DebtSettlement/DebtSettlementLine rows, and its undo
writes only a DebtSettlementReversal. None of them has an account foreign key
or calls sync_postings, so no account balance can move as a result.

Identity: a person is (normalized name, normalized contact). A debt-side group
and a loan-side group may be offset against each other only as a confirmed
pair (see `_pair_state`).
"""

from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlencode

from django.db import IntegrityError, transaction as db_transaction

from .models import (
    Borrowing,
    BorrowingRepayment,
    DebtSettlement,
    DebtSettlementLine,
    DebtSettlementReversal,
    Lending,
    LendingRepayment,
    PersonIdentityLink,
)
from .services import _refresh_borrowing_status, _refresh_lending_status

ZERO = Decimal("0.00")
_KEY_SEP = "\x1f"

CONFIRMED = "confirmed"
NEEDS_CONFIRMATION = "needs_confirmation"


def clean_name(value: str) -> str:
    """Collapse runs of whitespace and trim, keeping the user's capitalization."""
    return " ".join((value or "").split())


def norm(value: str) -> str:
    return clean_name(value).casefold()


def person_key(name: str, contact: str = "") -> str:
    return norm(name) + _KEY_SEP + norm(contact)


# --- Grouping -----------------------------------------------------------------


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
                "name_norm": norm(name),
                "contact": clean_name(record.contact),
                "contact_norm": norm(record.contact),
                "items": [],
                "total": ZERO,
                "offsettable": ZERO,
                "pairs": [],
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


# --- Identity matching --------------------------------------------------------


def _link_pair(name_norm, contact_a, contact_b):
    first, second = sorted((contact_a, contact_b))
    return (name_norm, first, second)


def _linked_pairs(user) -> set:
    return {(link.name_key, link.contact_a, link.contact_b) for link in PersonIdentityLink.objects.filter(user=user)}


def _pair_state(lender, borrower, links, lender_count, borrower_count):
    """How sure we are that these two same-named groups are one person.

    - None: contacts conflict (both present and different) — never combined.
    - CONFIRMED: contacts match and are present; or the user confirmed the
      link; or both are blank and nobody else shares the name on either side.
    - NEEDS_CONFIRMATION: one contact missing, or both blank while several
      people share the name (so the match could be the wrong John).
    """
    lc, bc = lender["contact_norm"], borrower["contact_norm"]
    if lc and bc and lc != bc:
        return None
    if _link_pair(lender["name_norm"], lc, bc) in links:
        return CONFIRMED, "link"
    if lc and lc == bc:
        return CONFIRMED, "contact"
    if not lc and not bc and lender_count == 1 and borrower_count == 1:
        return CONFIRMED, "name"
    return NEEDS_CONFIRMATION, None


def _pair_query(lender, borrower):
    return urlencode(
        {
            "name": lender["name"],
            "contact": lender["contact"],
            "other_contact": borrower["contact"],
        }
    )


def debt_positions(user) -> dict:
    """Active debts ("lenders") and loans ("borrowers") grouped one entry per
    person. Each group carries `pairs`: the opposite-side groups it could be
    offset against, each with a state (confirmed / needs confirmation)."""
    lenders = _group(_active_with_balance(Borrowing, user, "receiving_account"), "lender_name", "date_borrowed")
    borrowers = _group(_active_with_balance(Lending, user, "source_account"), "borrower_name", "date_lent")
    links = _linked_pairs(user)

    by_name = {}
    for group in lenders:
        by_name.setdefault(group["name_norm"], ([], []))[0].append(group)
    for group in borrowers:
        by_name.setdefault(group["name_norm"], ([], []))[1].append(group)

    for ls, bs in by_name.values():
        for lender in ls:
            for borrower in bs:
                result = _pair_state(lender, borrower, links, len(ls), len(bs))
                if result is None:
                    continue
                state, via = result
                both = min(lender["total"], borrower["total"])
                query = _pair_query(lender, borrower)
                for mine, other in ((lender, borrower), (borrower, lender)):
                    mine["pairs"].append(
                        {
                            "other": other,
                            "state": state,
                            "via": via,
                            "offsettable": both,
                            "query": query,
                            "name_is_shared": len(ls) > 1 or len(bs) > 1,
                        }
                    )
                    if state == CONFIRMED:
                        mine["offsettable"] = max(mine["offsettable"], both)
    return {"lenders": lenders, "borrowers": borrowers}


def find_pair(user, name, contact="", other_contact=None):
    """The (lender group, borrower group, pair info) for this identity, or None."""
    other_contact = contact if other_contact is None else other_contact
    positions = debt_positions(user)
    wanted_l = person_key(name, contact)
    wanted_b = person_key(name, other_contact)
    for lender in positions["lenders"]:
        if lender["key"] != wanted_l:
            continue
        for pair in lender["pairs"]:
            if pair["other"]["key"] == wanted_b:
                return lender, pair["other"], pair
    return None


class IdentityError(ValueError):
    pass


@db_transaction.atomic
def confirm_identity(*, user, name, contact="", other_contact=""):
    """Record that the lender-side and borrower-side groups are one person.
    Only allowed for a currently-pending pair, so two people whose contacts
    conflict can never be linked."""
    found = find_pair(user, name, contact, other_contact)
    if found is None:
        raise IdentityError("These two entries cannot be matched: their contacts conflict or one has no balance.")
    lender, borrower, pair = found
    if pair["state"] == CONFIRMED:
        return None
    link, _ = PersonIdentityLink.objects.get_or_create(
        user=user,
        name_key=lender["name_norm"],
        **dict(zip(("contact_a", "contact_b"), sorted((lender["contact_norm"], borrower["contact_norm"])))),
    )
    return link


@db_transaction.atomic
def unlink_identity(*, user, name, contact="", other_contact=""):
    first, second = sorted((norm(contact), norm(other_contact)))
    return PersonIdentityLink.objects.filter(
        user=user, name_key=norm(name), contact_a=first, contact_b=second
    ).delete()[0]


# --- Settlement ---------------------------------------------------------------


def _candidates(user, name, contact, other_contact, *, lock):
    """Active debts (matching `contact`) and loans (matching `other_contact`)
    with a balance for one name, oldest first."""
    debt_key = person_key(name, contact)
    loan_key = person_key(name, other_contact)
    b_qs = Borrowing.objects.filter(user=user, status=Borrowing.Status.ACTIVE).order_by("date_borrowed", "id")
    l_qs = Lending.objects.filter(user=user, status=Lending.Status.ACTIVE).order_by("date_lent", "id")
    if lock:
        b_qs, l_qs = b_qs.select_for_update(), l_qs.select_for_update()
    borrowings = [b for b in b_qs if person_key(b.lender_name, b.contact) == debt_key]
    lendings = [x for x in l_qs if person_key(x.borrower_name, x.contact) == loan_key]
    for record in (*borrowings, *lendings):
        record.outstanding_now = record.outstanding()
    return [b for b in borrowings if b.outstanding_now > 0], [x for x in lendings if x.outstanding_now > 0]


def settlement_summary(user, name, contact="", other_contact=None) -> dict:
    other_contact = contact if other_contact is None else other_contact
    borrowings, lendings = _candidates(user, name, contact, other_contact, lock=False)
    owed_by_me = sum((b.outstanding_now for b in borrowings), ZERO)
    owed_to_me = sum((x.outstanding_now for x in lendings), ZERO)
    found = find_pair(user, name, contact, other_contact)
    return {
        "borrowings": borrowings,
        "lendings": lendings,
        "owed_by_me": owed_by_me,
        "owed_to_me": owed_to_me,
        "maximum": min(owed_by_me, owed_to_me),
        "display_name": clean_name(borrowings[-1].lender_name) if borrowings else clean_name(name),
        "lender_contact": clean_name(contact),
        "borrower_contact": clean_name(other_contact),
        "state": found[2]["state"] if found else None,
        "via": found[2]["via"] if found else None,
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
def settle_with_person(*, user, name, contact="", other_contact=None, amount, date, notes="", token) -> DebtSettlement:
    """Offset up to min(owed by me, owed to me) against one confirmed person,
    oldest records first on each side. Rows are locked and re-read inside the
    transaction so the amount is checked against live balances, the identity is
    re-verified, and `token` makes a repeated submit return DuplicateSettlement
    instead of posting twice."""
    other_contact = contact if other_contact is None else other_contact
    duplicate = DebtSettlement.objects.filter(user=user, idempotency_key=token).first()
    if duplicate:
        raise DuplicateSettlement(duplicate)

    borrowings, lendings = _candidates(user, name, contact, other_contact, lock=True)
    if not borrowings or not lendings:
        raise ValueError("There is nothing to offset: this person must both owe you and be owed by you.")
    found = find_pair(user, name, contact, other_contact)
    if found is None or found[2]["state"] != CONFIRMED:
        raise IdentityError("Confirm that these entries are the same person before offsetting them.")
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


# --- Undo ---------------------------------------------------------------------


class AlreadyReversed(ValueError):
    pass


def reversal_blockers(settlement: DebtSettlement) -> list:
    """Reasons this settlement cannot be undone right now. Undo is last-in,
    first-out per record: if anything was recorded against the same debt or
    loan after the settlement (a repayment, or another still-active
    settlement), undoing it would rewrite the basis for that later activity."""
    reasons = []
    for line in settlement.lines.select_related("borrowing", "lending"):
        if line.borrowing_id:
            later_cash = BorrowingRepayment.objects.filter(
                borrowing_id=line.borrowing_id, created_at__gt=settlement.created_at
            ).exists()
            later_offsets = DebtSettlementLine.objects.filter(
                borrowing_id=line.borrowing_id, settlement_id__gt=settlement.id, settlement__reversal__isnull=True
            ).exists()
            label = f"the debt of {line.borrowing.amount:,.0f} on {line.borrowing.date_borrowed}"
        else:
            later_cash = LendingRepayment.objects.filter(
                lending_id=line.lending_id, created_at__gt=settlement.created_at
            ).exists()
            later_offsets = DebtSettlementLine.objects.filter(
                lending_id=line.lending_id, settlement_id__gt=settlement.id, settlement__reversal__isnull=True
            ).exists()
            label = f"the loan of {line.lending.amount:,.0f} on {line.lending.date_lent}"
        if later_cash:
            reasons.append(f"A repayment was recorded on {label} after this settlement.")
        if later_offsets:
            reasons.append(f"A later settlement also applies to {label}; undo that one first.")
    return reasons


@db_transaction.atomic
def reverse_settlement(*, user, settlement_id, date, notes="") -> DebtSettlementReversal:
    """Undo a settlement: add a reversal record (the settlement and its lines
    stay untouched) and reactivate any debt/loan it had fully covered."""
    settlement = DebtSettlement.objects.select_for_update().get(pk=settlement_id, user=user)
    if DebtSettlementReversal.objects.filter(settlement=settlement).exists():
        raise AlreadyReversed("This settlement was already undone.")
    if date < settlement.date:
        raise ValueError("The undo date cannot be earlier than the settlement it reverses.")

    lines = list(settlement.lines.select_related("borrowing", "lending"))
    borrowings = list(Borrowing.objects.select_for_update().filter(pk__in=[ln.borrowing_id for ln in lines if ln.borrowing_id]))
    lendings = list(Lending.objects.select_for_update().filter(pk__in=[ln.lending_id for ln in lines if ln.lending_id]))

    blockers = reversal_blockers(settlement)
    if blockers:
        raise ValueError(" ".join(blockers))

    try:
        with db_transaction.atomic():
            reversal = DebtSettlementReversal.objects.create(user=user, settlement=settlement, date=date, notes=notes)
    except IntegrityError:
        raise AlreadyReversed("This settlement was already undone.") from None

    for borrowing in borrowings:
        if borrowing.outstanding() > borrowing.total_owed():
            raise ValueError("Undoing this settlement would leave an incorrect balance.")
        _refresh_borrowing_status(borrowing)
    for lending in lendings:
        if lending.outstanding() > lending.amount:
            raise ValueError("Undoing this settlement would leave an incorrect balance.")
        _refresh_lending_status(lending)
    return reversal


# --- Renaming a person -------------------------------------------------------

LENDER, BORROWER = "lender", "borrower"  # dashboard columns: Borrowing / Lending records
_SIDES = {
    LENDER: (Borrowing, "lender_name", "Lenders"),
    BORROWER: (Lending, "borrower_name", "Borrowers"),
}


class RenameError(ValueError):
    pass


def _side(side):
    try:
        return _SIDES[side]
    except KeyError:
        raise RenameError("Unknown list.") from None


def rename_targets(user, side, name, contact="", *, pk=None, lock=False):
    """Records on one side belonging to exactly this person (normalized name AND
    contact), every status, or just record `pk` when renaming a single
    transaction. Another person who merely shares the name is never included."""
    model, name_attr, _ = _side(side)
    qs = model.objects.filter(user=user).order_by("id")
    if pk is not None:
        qs = qs.filter(pk=pk)
    if lock:
        qs = qs.select_for_update()
    wanted = person_key(name, contact)
    return [r for r in qs if person_key(getattr(r, name_attr), r.contact) == wanted]


def rename_preview(user, side, name, contact="", *, pk=None, new_name=None, new_contact=None) -> dict:
    records = rename_targets(user, side, name, contact, pk=pk)
    model, name_attr, _ = _side(side)
    active = [r for r in records if r.status == "active"]
    merges = False
    if new_name is not None and records:
        target = person_key(new_name, new_contact or "")
        moving = {r.pk for r in records}
        merges = any(
            r.pk not in moving and person_key(getattr(r, name_attr), r.contact) == target
            for r in model.objects.filter(user=user)
        )
    return {
        "records": records,
        "count": len(records),
        "active_count": len(active),
        "settled_count": len(records) - len(active),
        "total": sum((r.outstanding() for r in active), ZERO),
        "merges": merges,
    }


def _identity_exists(user, side, name, contact) -> bool:
    model, name_attr, _ = _side(side)
    key = person_key(name, contact)
    return any(person_key(getattr(r, name_attr), r.contact) == key for r in model.objects.filter(user=user))


def drop_stale_links(user, side, old_name, old_contact) -> int:
    """Remove identity links that referred to an identity nothing on `side`
    uses any more, so a stale confirmation can never match somebody new."""
    if _identity_exists(user, side, old_name, old_contact):
        return 0
    old_name_key, old_contact_key = norm(old_name), norm(old_contact)
    stale = PersonIdentityLink.objects.filter(user=user, name_key=old_name_key).filter(
        contact_a=old_contact_key
    ) | PersonIdentityLink.objects.filter(user=user, name_key=old_name_key, contact_b=old_contact_key)
    return stale.delete()[0]


@db_transaction.atomic
def rename_person(*, user, side, name, contact="", new_name, new_contact="", pk=None, expected_count=None) -> dict:
    """Change the name/contact on one person's records (or one record when `pk`
    is given). Only the two text fields and updated_at are written: amounts,
    repayments, settlements, reversals and account postings are untouched, and
    no settlement or identity link is created. Identity links that referred to
    the old identity are dropped once nothing on that side uses it any more, so
    a stale confirmation can never silently match somebody new."""
    model, name_attr, _ = _side(side)
    new_name, new_contact = clean_name(new_name), clean_name(new_contact)
    if not new_name:
        raise RenameError("Enter a name.")
    if len(new_name) > model._meta.get_field(name_attr).max_length:
        raise RenameError("That name is too long.")
    contact_field = model._meta.get_field("contact")
    if len(new_contact) > contact_field.max_length:
        raise RenameError("That contact is too long.")

    records = rename_targets(user, side, name, contact, pk=pk, lock=True)
    if not records:
        raise RenameError("Those entries no longer exist. Reload the page and try again.")
    if expected_count is not None and expected_count != len(records):
        raise RenameError(
            f"This person now has {len(records)} transaction{'s' if len(records) != 1 else ''}, not {expected_count}. "
            "Review and submit again."
        )

    old_name, old_contact = clean_name(getattr(records[0], name_attr)), clean_name(records[0].contact)
    changed = []
    for record in records:
        before = (getattr(record, name_attr), record.contact)
        if before == (new_name, new_contact):
            continue
        setattr(record, name_attr, new_name)
        record.contact = new_contact
        record.save(update_fields=[name_attr, "contact", "updated_at"])
        changed.append({"id": record.pk, "from_name": before[0], "from_contact": before[1]})

    links_removed = 0
    if changed and person_key(old_name, old_contact) != person_key(new_name, new_contact):
        links_removed = drop_stale_links(user, side, old_name, old_contact)
    return {
        "side": side,
        "count": len(records),
        "changed": changed,
        "old_name": old_name,
        "old_contact": old_contact,
        "new_name": new_name,
        "new_contact": new_contact,
        "links_removed": links_removed,
        "record_ids": [r.pk for r in records],
    }
