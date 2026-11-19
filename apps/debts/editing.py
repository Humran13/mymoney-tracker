"""Editing existing debt transactions and their payments.

Nothing here invents a second accounting mechanism. A debt edit changes the
record and then replays the *same* posting builders used at creation
(`services._borrowing_postings` / `_lending_postings` / `_repayment_postings` /
`_lending_repayment_postings`) through `sync_postings`, which atomically
replaces that one source object's postings. Balances therefore stay
`opening_balance + sum(postings)`; nothing is adjusted in place.

Every edit has two phases that share one validator (`plan_*`):

* plan  - read-only. Validates, builds the field diff and computes the exact
          per-account balance change (respecting each account's starting date),
          so the UI can show a confirmation before anything is written.
* apply - atomic. Locks the parent record, re-runs the same validator against
          the locked, current figures, then writes. Any failure raises
          `EditError` and the transaction rolls back, leaving records and
          balances untouched.
"""

import copy
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction as db_transaction

from apps.accounts.models import AccountPosting, balance_start_date
from apps.accounts.services import account_balance, sync_postings

from . import services
from .models import Borrowing, BorrowingRepayment, Lending, LendingRepayment
from .settlements import BORROWER, LENDER, ZERO, clean_name, drop_stale_links, person_key

CENT = Decimal("0.01")

MONEY_FIELDS = {"amount", "interest_amount", "debt_type"}


class EditError(ValueError):
    """A change that cannot be made safely; the message is shown to the user."""


def money(value) -> str:
    return f"{Decimal(value or 0):,.2f}"


# --- shared helpers ---------------------------------------------------------


def _effective(account, amount, occurred_on):
    """What a posting contributes to `account_balance`: postings dated before
    the account's starting date are history already inside the opening balance."""
    return amount if occurred_on >= balance_start_date(account) else ZERO


def posting_delta(source_obj, new_specs) -> dict:
    """{account: change in account_balance} if `source_obj`'s postings were
    replaced by `new_specs`. Uses the real stored postings as the 'before'."""
    content_type = ContentType.objects.get_for_model(source_obj)
    delta = {}
    stored = AccountPosting.objects.filter(content_type=content_type, object_id=source_obj.pk).select_related("account")
    for posting in stored:
        delta[posting.account] = delta.get(posting.account, ZERO) - _effective(
            posting.account, posting.amount, posting.occurred_on
        )
    for spec in new_specs:
        delta[spec["account"]] = delta.get(spec["account"], ZERO) + _effective(
            spec["account"], spec["amount"], spec["occurred_on"]
        )
    return {account: change for account, change in delta.items() if change != 0}


def describe_balance_changes(delta) -> list:
    rows = []
    for account, change in sorted(delta.items(), key=lambda item: item[0].name.casefold()):
        current = account_balance(account)
        rows.append({"account": account, "change": change, "before": current, "after": current + change})
    return rows


def _history_warnings(specs) -> list:
    notes = []
    for spec in specs:
        start = balance_start_date(spec["account"])
        if spec["occurred_on"] < start:
            notes.append(
                f"{spec['account'].name} starts on {start:%d %b %Y}. This is dated before that, so it is kept "
                "as history and does not change the account's current balance."
            )
    return sorted(set(notes))


def _check_account(user, account):
    if account is None or account.user_id != user.pk:
        raise EditError("Choose one of your own accounts.")


def _show(value):
    if value is None or value == "":
        return "-"
    if isinstance(value, Decimal):
        return money(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "name") and hasattr(value, "pk"):
        return value.name
    return str(value)


# --- debt transactions ------------------------------------------------------

_CONFIG = {
    LENDER: dict(
        model=Borrowing, name="lender_name", date="date_borrowed", account="receiving_account",
        fields=("lender_name", "debt_type", "amount", "date_borrowed", "receiving_account", "due_date", "interest_amount", "notes"),
        postings=services._borrowing_postings, refresh=services._refresh_borrowing_status,
    ),
    BORROWER: dict(
        model=Lending, name="borrower_name", date="date_lent", account="source_account",
        fields=("borrower_name", "debt_type", "amount", "date_lent", "source_account", "due_date", "interest_amount", "notes"),
        postings=services._lending_postings, refresh=services._refresh_lending_status,
    ),
}

_LABELS = {
    "lender_name": "Person", "borrower_name": "Person", "debt_type": "Debt type", "amount": "Amount",
    "date_borrowed": "Date", "date_lent": "Date", "receiving_account": "Account", "source_account": "Account",
    "due_date": "Due date", "interest_amount": "Interest", "notes": "Notes",
}


def _config(side):
    try:
        return _CONFIG[side]
    except KeyError:
        raise EditError("Unknown list.") from None


def _load(user, side, pk, *, lock=False):
    model = _config(side)["model"]
    qs = model.objects.filter(user=user)
    if lock:
        qs = qs.select_for_update()
    try:
        return qs.get(pk=pk)
    except model.DoesNotExist:
        raise EditError("That transaction no longer exists.") from None


def _earliest_activity(record, side):
    """Earliest date of any payment or active (unreversed) offset on the record."""
    dates = list(record.repayments.values_list("date", flat=True))
    dates += list(record.offset_lines.filter(settlement__reversal__isnull=True).values_list("settlement__date", flat=True))
    return min(dates) if dates else None


def plan_edit(user, side, pk, changes, *, record=None) -> dict:
    """Validate an edit and describe its effect without writing anything."""
    cfg = _config(side)
    record = record or _load(user, side, pk)
    new = copy.copy(record)
    for field in cfg["fields"] + ("contact",):
        if field in changes:
            setattr(new, field, changes[field])

    name_attr, date_attr, account_attr = cfg["name"], cfg["date"], cfg["account"]
    setattr(new, name_attr, clean_name(getattr(new, name_attr)))
    if not getattr(new, name_attr):
        raise EditError("Enter a name.")
    if new.amount is None or new.amount <= 0:
        raise EditError("The amount must be greater than zero.")
    if new.interest_amount is None or new.interest_amount < 0:
        raise EditError("Interest cannot be negative.")

    if new.debt_type == cfg["model"].DebtType.CASH_LOAN:
        _check_account(user, getattr(new, account_attr))
    else:
        setattr(new, account_attr, None)

    settled_away = record.total_offset()
    if side == LENDER:
        already_applied = record.total_repaid() + settled_away
        floor = already_applied - new.interest_amount
        total_label = "total owed (amount plus interest)"
    else:
        already_applied = record.total_principal_received() + settled_away
        floor = already_applied
        total_label = "amount lent"
    outstanding_before = record.outstanding()
    outstanding_after = new.outstanding()
    if outstanding_after < 0:
        raise EditError(
            f"{money(already_applied)} has already been repaid or offset on this transaction, so the {total_label} "
            f"cannot go below that. The smallest amount you can enter is {money(max(floor, CENT))}."
        )

    new_date = getattr(new, date_attr)
    if new_date != getattr(record, date_attr):
        earliest = _earliest_activity(record, side)
        if earliest is not None and new_date > earliest:
            raise EditError(
                f"A payment or offset was already recorded on {earliest:%d %b %Y}, so the date cannot be moved "
                "after it. Choose an earlier date or edit that payment first."
            )

    old_key = person_key(getattr(record, name_attr), record.contact)
    new_key = person_key(getattr(new, name_attr), new.contact)
    identity_changed = old_key != new_key
    merges_into_existing = False
    if identity_changed:
        if record.offset_lines.filter(settlement__reversal__isnull=True).exists():
            raise EditError(
                "This transaction took part in a noncash offset with this person, so it cannot be moved to a "
                "different person. Undo the settlement first, or change the other details."
            )
        merges_into_existing = any(
            person_key(getattr(other, name_attr), other.contact) == new_key
            for other in cfg["model"].objects.filter(user=user).exclude(pk=record.pk)
        )

    specs = cfg["postings"](new)
    delta = posting_delta(record, specs)

    diff = []
    for field in cfg["fields"]:
        before, after = getattr(record, field), getattr(new, field)
        if field == account_attr:
            before, after = getattr(record, account_attr), getattr(new, account_attr)
            same = (before.pk if before else None) == (after.pk if after else None)
        elif field == "debt_type":
            same = before == after
            before, after = record.get_debt_type_display(), new.get_debt_type_display()
        else:
            same = before == after
        if not same:
            diff.append({"field": field, "label": _LABELS[field], "before": _show(before), "after": _show(after)})

    changed_fields = {item["field"] for item in diff}
    return {
        "record": record,
        "new": new,
        "diff": diff,
        "balance_changes": describe_balance_changes(delta),
        "has_balance_change": bool(delta),
        "warnings": _history_warnings(specs),
        "outstanding_before": outstanding_before,
        "outstanding_after": outstanding_after,
        "identity_changed": identity_changed,
        "merges_into_existing": merges_into_existing,
        "needs_confirmation": bool(delta) or bool(changed_fields & MONEY_FIELDS) or identity_changed,
    }


@db_transaction.atomic
def apply_edit(*, user, side, pk, changes, version=None) -> dict:
    """Lock the record, re-validate against the live figures, then save the
    record and replace its postings in the same database transaction."""
    cfg = _config(side)
    record = _load(user, side, pk, lock=True)
    if version and record.updated_at.isoformat() != version:
        raise EditError("This transaction was changed elsewhere after you opened it. Reload it and try again.")
    plan = plan_edit(user, side, pk, changes, record=record)
    if not plan["diff"]:
        return plan
    new = plan["new"]
    new.full_clean()
    new.save()
    sync_postings(new, cfg["postings"](new), user=user)
    cfg["refresh"](new)
    if plan["identity_changed"]:
        drop_stale_links(user, side, getattr(plan["record"], cfg["name"]), plan["record"].contact)
    plan["new"] = new
    return plan


def audit_metadata(plan, **extra) -> dict:
    return {"changes": [{k: item[k] for k in ("label", "before", "after")} for item in plan["diff"]], **extra}


# --- payments ----------------------------------------------------------------

_PAYMENT = {
    LENDER: dict(
        model=BorrowingRepayment, parent="borrowing", account="paying_account",
        fields=("amount", "fee_amount", "date", "paying_account", "notes"),
    ),
    BORROWER: dict(
        model=LendingRepayment, parent="lending", account="receiving_account",
        fields=("principal_amount", "interest_amount", "date", "receiving_account", "notes"),
    ),
}

_PAYMENT_LABELS = {
    "amount": "Payment amount", "fee_amount": "Transaction fee", "principal_amount": "Principal",
    "interest_amount": "Interest", "date": "Date", "paying_account": "Account", "receiving_account": "Account",
    "notes": "Notes",
}


def _load_payment(user, side, pk, *, lock=False):
    model = _PAYMENT[side]["model"]
    qs = model.objects.filter(user=user)
    if lock:
        qs = qs.select_for_update()
    try:
        return qs.get(pk=pk)
    except model.DoesNotExist:
        raise EditError("That payment no longer exists.") from None


def plan_payment_edit(user, side, pk, changes, *, payment=None) -> dict:
    cfg = _PAYMENT[side]
    payment = payment or _load_payment(user, side, pk)
    parent = getattr(payment, cfg["parent"])
    new = copy.copy(payment)
    for field in cfg["fields"]:
        if field in changes:
            setattr(new, field, changes[field])
    _check_account(user, getattr(new, cfg["account"]))

    if side == LENDER:
        fee = new.fee_amount or ZERO
        if new.amount is None or new.amount <= 0:
            raise EditError("Repayment amount must be greater than zero.")
        if fee < 0:
            raise EditError("Transaction fee cannot be negative.")
        new.fee_amount = fee
        available = services._available_to_repay(parent, exclude_repayment=payment)
        applied = new.amount
        specs = services._repayment_postings(new)
    else:
        principal, interest = new.principal_amount or ZERO, new.interest_amount or ZERO
        if principal < 0 or interest < 0:
            raise EditError("Amounts cannot be negative.")
        if principal <= 0 and interest <= 0:
            raise EditError("Enter a principal and/or interest amount received.")
        new.principal_amount, new.interest_amount = principal, interest
        available = parent.outstanding() + payment.principal_amount
        applied = principal
        specs = services._lending_repayment_postings(new)
    if applied > available:
        raise EditError(
            f"That is more than the {money(available)} this transaction can still take. "
            "A payment can never exceed what is outstanding."
        )
    parent_date = parent.date_borrowed if side == LENDER else parent.date_lent
    if new.date != payment.date and new.date < parent_date:
        raise EditError(f"A payment cannot be dated before the transaction it pays ({parent_date:%d %b %Y}).")

    delta = posting_delta(payment, specs)
    diff = []
    for field in cfg["fields"]:
        before, after = getattr(payment, field), getattr(new, field)
        if field == cfg["account"]:
            same = before.pk == after.pk
        else:
            same = before == after
        if not same:
            diff.append({"field": field, "label": _PAYMENT_LABELS[field], "before": _show(before), "after": _show(after)})
    outstanding_before = available - (payment.amount if side == LENDER else payment.principal_amount)
    outstanding_after = available - applied
    return {
        "payment": payment,
        "new": new,
        "parent": parent,
        "diff": diff,
        "balance_changes": describe_balance_changes(delta),
        "has_balance_change": bool(delta),
        "warnings": _history_warnings(specs),
        "outstanding_before": outstanding_before,
        "outstanding_after": outstanding_after,
        "needs_confirmation": bool(delta) or outstanding_after != outstanding_before,
    }


@db_transaction.atomic
def apply_payment_edit(*, user, side, pk, changes) -> dict:
    cfg = _PAYMENT[side]
    # Lock the parent debt first (the same order every payment flow uses), then the payment.
    parent_id = getattr(_load_payment(user, side, pk), f"{cfg['parent']}_id")
    (Borrowing if side == LENDER else Lending).objects.select_for_update().get(pk=parent_id)
    payment = _load_payment(user, side, pk, lock=True)
    plan = plan_payment_edit(user, side, pk, changes, payment=payment)
    if not plan["diff"]:
        return plan
    values = {field: getattr(plan["new"], field) for field in cfg["fields"]}
    try:
        if side == LENDER:
            services.update_borrowing_repayment(payment, **values)
        else:
            services.update_lending_repayment(payment, **values)
    except ValueError as exc:
        raise EditError(str(exc)) from exc
    return plan
