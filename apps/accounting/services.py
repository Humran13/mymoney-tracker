from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AccountPosting, balance_start_date
from apps.accounts.services import delete_postings, sync_postings
from apps.core.audit import log_audit_event
from apps.core.models import IdempotencyKey

from .models import (
    HeldFund,
    HeldFundEntry,
    InventoryLot,
    InventorySale,
    InventorySalePayment,
    ReimbursementClaim,
    ReimbursementPayment,
)

ZERO = Decimal("0.00")


def _claim_operation(user, key, scope):
    if key is None:
        return
    _, created = IdempotencyKey.objects.get_or_create(user=user, key=key, defaults={"scope": scope})
    if not created:
        raise ValidationError("This financial operation was already submitted.")


def _positive(value, label="Amount"):
    if value is None or value <= 0:
        raise ValidationError(f"{label} must be greater than zero.")


def _nonnegative(value, label="Amount"):
    if value is None or value < 0:
        raise ValidationError(f"{label} cannot be negative.")


def _account_date(account, day, user=None):
    if account.user_id is None:
        raise ValidationError("A valid account is required.")
    if user is not None and account.user_id != user.id:
        raise ValidationError("The account does not belong to this user.")
    if account.currency_code != "UGX":
        raise ValidationError(
            "Agency, reimbursement, and resale records currently require a UGX account. "
            "Enter an explicit UGX-equivalent amount in a UGX account; currencies are never converted silently."
        )
    if day < balance_start_date(account):
        raise ValidationError("This date is before the account's opening-balance date.")


def _audit(actor, action, obj, description, before=None):
    metadata = {"after": _snapshot(obj)}
    if before is not None:
        metadata["before"] = before
    log_audit_event(actor, action, affected_user=obj.user, obj=obj, description=description, metadata=metadata)


def _snapshot(obj):
    data = {}
    for field in obj._meta.concrete_fields:
        value = getattr(obj, field.attname)
        data[field.name] = str(value) if value is not None else None
    return data


def _save(model, *, user, actor=None, **fields):
    obj = model(user=user, **fields)
    obj.full_clean()
    obj.save()
    _audit(actor or user, "create", obj, f"Created {model._meta.verbose_name}")
    return obj


@transaction.atomic
def create_held_fund(*, user, actor=None, **fields):
    return _save(HeldFund, user=user, actor=actor, **fields)


@transaction.atomic
def create_held_fund_with_receipt(
    *, user, actor=None, owner_name, purpose="", fund_notes="", operation_key=None, **entry_fields
):
    _claim_operation(user, operation_key, "held_fund_first_receipt")
    fund = _save(
        HeldFund,
        user=user,
        actor=actor,
        owner_name=owner_name,
        purpose=purpose,
        notes=fund_notes,
    )
    entry = save_held_entry(
        user=user,
        actor=actor,
        fund=fund,
        kind=HeldFundEntry.Kind.RECEIPT,
        **entry_fields,
    )
    return fund, entry


@transaction.atomic
def update_held_fund(fund, *, actor=None, **fields):
    fund = HeldFund.objects.select_for_update().get(pk=fund.pk, user=fund.user)
    before = _snapshot(fund)
    for key, value in fields.items():
        setattr(fund, key, value)
    fund.full_clean()
    fund.save()
    _audit(actor or fund.user, "update", fund, "Edited held-funds record", before)
    return fund


def _held_specs(entry):
    direction = Decimal("1") if entry.kind == HeldFundEntry.Kind.RECEIPT else Decimal("-1")
    source = AccountPosting.SourceType.HELD_RECEIVE if direction > 0 else AccountPosting.SourceType.HELD_DISTRIBUTE
    specs = [dict(account=entry.account, amount=direction * entry.amount, source_type=source,
                  occurred_on=entry.date, description=entry.description or entry.fund.owner_name)]
    if entry.fee_amount:
        fee_type = AccountPosting.SourceType.HELD_OWNER_FEE if entry.fee_bearer == HeldFundEntry.FeeBearer.OWNER else AccountPosting.SourceType.FEE
        specs.append(dict(account=entry.account, amount=-entry.fee_amount, source_type=fee_type,
                          occurred_on=entry.date, description=f"Fee: {entry.description or entry.fund.owner_name}"))
    return specs


def _validate_held_history(fund):
    balance = ZERO
    entries = fund.entries.filter(reversed_at__isnull=True).order_by("date", "id")
    for entry in entries:
        if entry.kind == HeldFundEntry.Kind.RECEIPT:
            balance += entry.amount
        else:
            balance -= entry.amount
        if entry.fee_bearer == HeldFundEntry.FeeBearer.OWNER:
            balance -= entry.fee_amount
        if balance < ZERO:
            raise ValidationError(
                "This change would make held funds negative at an earlier point in the accounting history."
            )
    return balance


@transaction.atomic
def save_held_entry(*, user, actor=None, instance=None, **fields):
    operation_key = fields.pop("operation_key", None)
    if instance is None:
        _claim_operation(user, operation_key, "held_fund_entry")
    requested_fund = fields.get("fund", getattr(instance, "fund", None))
    requested_fund_id = requested_fund.pk
    fund_ids = {requested_fund_id}
    if instance is not None:
        fund_ids.add(instance.fund_id)
    funds = {
        fund.pk: fund
        for fund in HeldFund.objects.select_for_update().filter(pk__in=fund_ids, user=user).order_by("pk")
    }
    if set(funds) != fund_ids:
        raise ValidationError("The held-funds record does not belong to this user.")
    for locked_fund in funds.values():
        list(locked_fund.entries.select_for_update().filter(reversed_at__isnull=True).order_by("pk"))
    fund = funds[requested_fund_id]
    account = fields.get("account", getattr(instance, "account", None))
    day = fields.get("date", getattr(instance, "date", None))
    _account_date(account, day, user)
    _positive(fields.get("amount", getattr(instance, "amount", None)))
    _nonnegative(fields.get("fee_amount", getattr(instance, "fee_amount", ZERO)), "Fee")
    if instance:
        entry = HeldFundEntry.objects.select_for_update().get(pk=instance.pk, user=user, reversed_at__isnull=True)
        before = _snapshot(entry)
        for key, value in fields.items():
            setattr(entry, key, value)
    else:
        entry = HeldFundEntry(user=user, **fields)
        before = None
    entry.fund = fund
    entry.full_clean()
    entry.save()
    for locked_fund in funds.values():
        _validate_held_history(locked_fund)
    sync_postings(entry, _held_specs(entry), user=user)
    _audit(actor or user, "update" if instance else "create", entry, "Edited held-funds movement" if instance else "Recorded held-funds movement", before)
    return entry


@transaction.atomic
def create_reimbursement(*, user, actor=None, **fields):
    _claim_operation(user, fields.pop("operation_key", None), "reimbursement_purchase")
    _account_date(fields["account"], fields["purchase_date"], user)
    _positive(fields["purchase_amount"])
    _nonnegative(fields.get("fee_amount", ZERO), "Fee")
    claim = _save(ReimbursementClaim, user=user, actor=actor, **fields)
    specs = [dict(account=claim.account, amount=-claim.purchase_amount, source_type=AccountPosting.SourceType.REIMBURSE_BUY,
                  occurred_on=claim.purchase_date, description=claim.description)]
    if claim.fee_amount:
        fee_type = AccountPosting.SourceType.REIMBURSE_BUY if claim.fee_bearer == HeldFundEntry.FeeBearer.OWNER else AccountPosting.SourceType.FEE
        specs.append(dict(account=claim.account, amount=-claim.fee_amount, source_type=fee_type,
                          occurred_on=claim.purchase_date, description=f"Purchase fee: {claim.description}"))
    sync_postings(claim, specs, user=user)
    return claim


@transaction.atomic
def update_reimbursement(claim, *, actor=None, **fields):
    fields.pop("operation_key", None)
    claim = ReimbursementClaim.objects.select_for_update().get(pk=claim.pk, user=claim.user, reversed_at__isnull=True)
    before = _snapshot(claim)
    for key, value in fields.items():
        setattr(claim, key, value)
    _account_date(claim.account, claim.purchase_date, claim.user)
    _positive(claim.purchase_amount)
    payments = claim.payments.filter(reversed_at__isnull=True)
    settled = sum((p.amount + p.unrecovered_amount for p in payments), ZERO)
    owner_fees = sum(
        (p.fee_amount for p in payments if p.fee_bearer == HeldFundEntry.FeeBearer.OWNER), ZERO
    )
    if claim.original_receivable() + owner_fees - settled < ZERO:
        raise ValidationError("The purchase cannot be reduced below reimbursements and write-offs already recorded.")
    claim.full_clean()
    claim.save()
    specs = [dict(account=claim.account, amount=-claim.purchase_amount, source_type=AccountPosting.SourceType.REIMBURSE_BUY,
                  occurred_on=claim.purchase_date, description=claim.description)]
    if claim.fee_amount:
        fee_type = AccountPosting.SourceType.REIMBURSE_BUY if claim.fee_bearer == HeldFundEntry.FeeBearer.OWNER else AccountPosting.SourceType.FEE
        specs.append(dict(account=claim.account, amount=-claim.fee_amount, source_type=fee_type,
                          occurred_on=claim.purchase_date, description=f"Purchase fee: {claim.description}"))
    sync_postings(claim, specs, user=claim.user)
    _audit(actor or claim.user, "update", claim, "Edited reimbursable purchase", before)
    return claim


@transaction.atomic
def save_reimbursement_payment(*, user, actor=None, instance=None, **fields):
    operation_key = fields.pop("operation_key", None)
    if instance is None:
        _claim_operation(user, operation_key, "reimbursement_payment")
    requested_claim = fields.get("claim", getattr(instance, "claim", None))
    if instance is not None and requested_claim.pk != instance.claim_id:
        raise ValidationError("An existing reimbursement payment cannot be moved to another claim.")
    claim = ReimbursementClaim.objects.select_for_update().get(
        pk=requested_claim.pk, user=user, reversed_at__isnull=True
    )
    account = fields.get("account", getattr(instance, "account", None))
    day = fields.get("date", getattr(instance, "date", None))
    _account_date(account, day, user)
    _positive(fields.get("amount", getattr(instance, "amount", None)))
    _nonnegative(fields.get("unrecovered_amount", getattr(instance, "unrecovered_amount", ZERO)), "Unrecovered amount")
    _nonnegative(fields.get("fee_amount", getattr(instance, "fee_amount", ZERO)), "Fee")
    if instance:
        payment = ReimbursementPayment.objects.select_for_update().get(pk=instance.pk, user=user, reversed_at__isnull=True)
        if payment.claim_id != claim.id:
            raise ValidationError("An existing reimbursement payment cannot be moved to another claim.")
        base_outstanding = claim.outstanding() + payment.amount + payment.unrecovered_amount
        if payment.fee_bearer == HeldFundEntry.FeeBearer.OWNER:
            base_outstanding -= payment.fee_amount
        before = _snapshot(payment)
        for key, value in fields.items():
            setattr(payment, key, value)
    else:
        payment = ReimbursementPayment(user=user, **fields)
        base_outstanding = claim.outstanding()
        before = None
    payment.claim = claim
    projected_outstanding = base_outstanding - payment.amount - payment.unrecovered_amount
    if payment.fee_bearer == HeldFundEntry.FeeBearer.OWNER:
        projected_outstanding += payment.fee_amount
    if projected_outstanding < ZERO:
        raise ValidationError("Reimbursement and write-off cannot exceed the outstanding receivable.")
    payment.full_clean()
    payment.save()
    specs = [dict(account=payment.account, amount=payment.amount, source_type=AccountPosting.SourceType.REIMBURSE_IN,
                  occurred_on=payment.date, description=f"Reimbursement from {claim.responsible_party}")]
    if payment.fee_amount:
        fee_type = AccountPosting.SourceType.REIMBURSE_BUY if payment.fee_bearer == HeldFundEntry.FeeBearer.OWNER else AccountPosting.SourceType.FEE
        specs.append(dict(account=payment.account, amount=-payment.fee_amount, source_type=fee_type,
                          occurred_on=payment.date, description=f"Reimbursement fee: {claim.responsible_party}"))
    sync_postings(payment, specs, user=user)
    _audit(actor or user, "update" if instance else "create", payment, "Edited reimbursement payment" if instance else "Recorded reimbursement payment", before)
    return payment


def _lot_specs(lot):
    return [dict(account=lot.account, amount=-lot.landed_cost, source_type=AccountPosting.SourceType.INVENTORY_BUY,
                 occurred_on=lot.acquisition_date, description=f"Inventory: {lot.product_name}")]


@transaction.atomic
def save_inventory_lot(*, user, actor=None, instance=None, **fields):
    operation_key = fields.pop("operation_key", None)
    if instance is None:
        _claim_operation(user, operation_key, "inventory_purchase")
    account = fields.get("account", getattr(instance, "account", None))
    day = fields.get("acquisition_date", getattr(instance, "acquisition_date", None))
    _account_date(account, day, user)
    for key in ("quantity", "purchase_cost"):
        _positive(fields.get(key, getattr(instance, key, None)), key.replace("_", " ").title())
    for key in ("shipping_cost", "customs_cost", "transport_cost", "other_cost"):
        _nonnegative(fields.get(key, getattr(instance, key, ZERO)), key.replace("_", " ").title())
    if instance:
        lot = InventoryLot.objects.select_for_update().get(pk=instance.pk, user=user, reversed_at__isnull=True)
        before = _snapshot(lot)
        immutable_after_sale = (
            "quantity", "purchase_cost", "shipping_cost", "customs_cost", "transport_cost", "other_cost",
        )
        if lot.sales.filter(reversed_at__isnull=True).exists() and any(
            key in fields and fields[key] != getattr(lot, key) for key in immutable_after_sale
        ):
            raise ValidationError(
                "Inventory quantity and cost cannot be changed after a sale. "
                "Reverse the sales first or record a separate correcting inventory lot."
            )
        earliest_sale = lot.sales.filter(reversed_at__isnull=True).order_by("sale_date").first()
        if earliest_sale and day > earliest_sale.sale_date:
            raise ValidationError("The purchase date cannot be later than a sale already recorded from this lot.")
        for key, value in fields.items():
            setattr(lot, key, value)
        if lot.quantity < lot.quantity_sold():
            raise ValidationError("Quantity cannot be reduced below the quantity already sold.")
    else:
        lot = InventoryLot(user=user, **fields)
        before = None
    lot.full_clean()
    lot.save()
    sync_postings(lot, _lot_specs(lot), user=user)
    _audit(actor or user, "update" if instance else "create", lot, "Edited inventory purchase" if instance else "Recorded inventory purchase", before)
    return lot


@transaction.atomic
def save_inventory_sale(*, user, actor=None, instance=None, **fields):
    operation_key = fields.pop("operation_key", None)
    if instance is None:
        _claim_operation(user, operation_key, "inventory_sale")
    requested_lot = fields.get("lot", getattr(instance, "lot", None))
    if instance is not None and requested_lot.pk != instance.lot_id:
        raise ValidationError("An existing inventory sale cannot be moved to another inventory lot.")
    lot = InventoryLot.objects.select_for_update().get(
        pk=requested_lot.pk, user=user, reversed_at__isnull=True
    )
    _positive(fields.get("quantity", getattr(instance, "quantity", None)), "Quantity")
    _positive(fields.get("sale_amount", getattr(instance, "sale_amount", None)), "Sale amount")
    if instance:
        sale = InventorySale.objects.select_for_update().get(pk=instance.pk, user=user, reversed_at__isnull=True)
        if sale.lot_id != lot.id:
            raise ValidationError("An existing inventory sale cannot be moved to another inventory lot.")
        available = lot.quantity_remaining() + sale.quantity
        before = _snapshot(sale)
        for key, value in fields.items():
            setattr(sale, key, value)
        if sale.sale_amount - sale.amount_paid() < ZERO:
            raise ValidationError("Sale amount cannot be reduced below payments already received.")
    else:
        sale = InventorySale(user=user, unit_cost_at_sale=lot.unit_cost, **fields)
        available = lot.quantity_remaining()
        before = None
    sale.lot = lot
    if sale.unit_cost_at_sale is None:
        sale.unit_cost_at_sale = lot.unit_cost
    if sale.sale_date < lot.acquisition_date:
        raise ValidationError("A sale cannot be dated before the inventory was purchased.")
    if available - sale.quantity < Decimal("0.000"):
        raise ValidationError("Sale quantity exceeds inventory currently available.")
    sale.full_clean()
    sale.save()
    _audit(actor or user, "update" if instance else "create", sale, "Edited inventory sale" if instance else "Recorded inventory sale", before)
    return sale


@transaction.atomic
def save_sale_payment(*, user, actor=None, instance=None, **fields):
    operation_key = fields.pop("operation_key", None)
    if instance is None:
        _claim_operation(user, operation_key, "inventory_sale_payment")
    requested_sale = fields.get("sale", getattr(instance, "sale", None))
    if instance is not None and requested_sale.pk != instance.sale_id:
        raise ValidationError("An existing customer payment cannot be moved to another sale.")
    sale = InventorySale.objects.select_for_update().get(
        pk=requested_sale.pk, user=user, reversed_at__isnull=True
    )
    account = fields.get("account", getattr(instance, "account", None))
    day = fields.get("date", getattr(instance, "date", None))
    _account_date(account, day, user)
    _positive(fields.get("amount", getattr(instance, "amount", None)))
    _nonnegative(fields.get("fee_amount", getattr(instance, "fee_amount", ZERO)), "Fee")
    if instance:
        payment = InventorySalePayment.objects.select_for_update().get(pk=instance.pk, user=user, reversed_at__isnull=True)
        if payment.sale_id != sale.id:
            raise ValidationError("An existing customer payment cannot be moved to another sale.")
        base_outstanding = sale.outstanding() + payment.amount
        before = _snapshot(payment)
        for key, value in fields.items():
            setattr(payment, key, value)
    else:
        payment = InventorySalePayment(user=user, **fields)
        base_outstanding = sale.outstanding()
        before = None
    payment.sale = sale
    if base_outstanding - payment.amount < ZERO:
        raise ValidationError("Payment cannot exceed the customer's outstanding balance.")
    payment.full_clean()
    payment.save()
    specs = [dict(account=payment.account, amount=payment.amount, source_type=AccountPosting.SourceType.SALE_RECEIVE,
                  occurred_on=payment.date, description=f"Sale payment: {sale.lot.product_name}")]
    if payment.fee_amount:
        specs.append(dict(account=payment.account, amount=-payment.fee_amount, source_type=AccountPosting.SourceType.BUSINESS_FEE,
                          occurred_on=payment.date, description=f"Sale fee: {sale.lot.product_name}"))
    sync_postings(payment, specs, user=user)
    _audit(actor or user, "update" if instance else "create", payment, "Edited sale payment" if instance else "Recorded sale payment", before)
    return payment


@transaction.atomic
def reverse(obj, *, actor, reason):
    model = type(obj)
    held_fund = None
    if isinstance(obj, HeldFundEntry):
        held_fund = HeldFund.objects.select_for_update().get(pk=obj.fund_id, user=actor)
        list(held_fund.entries.select_for_update().filter(reversed_at__isnull=True).order_by("pk"))
    locked = model.objects.select_for_update().get(pk=obj.pk, user=actor, reversed_at__isnull=True)
    before = _snapshot(locked)
    if isinstance(locked, InventoryLot) and locked.sales.filter(reversed_at__isnull=True).exists():
        raise ValidationError("Reverse this lot's sales before reversing the inventory purchase.")
    if isinstance(locked, ReimbursementClaim) and locked.payments.filter(reversed_at__isnull=True).exists():
        raise ValidationError("Reverse its reimbursement payments before reversing the purchase.")
    if isinstance(locked, InventorySale) and locked.payments.filter(reversed_at__isnull=True).exists():
        raise ValidationError("Reverse its customer payments before reversing the sale.")
    locked.reversed_at = timezone.now()
    locked.reversal_reason = reason
    locked.save(update_fields=["reversed_at", "reversal_reason", "updated_at"])
    if held_fund is not None:
        _validate_held_history(held_fund)
    delete_postings(locked)
    _audit(actor, "update", locked, f"Reversed {model._meta.verbose_name}", before)
    return locked


@transaction.atomic
def create_attachment(*, user, actor=None, target, label, document):
    from .models import AccountingAttachment

    attachment = AccountingAttachment(user=user, target=target, label=label, document=document)
    attachment.full_clean()
    attachment.save()
    _audit(actor or user, "create", attachment, "Attached supporting accounting document")
    return attachment


@transaction.atomic
def delete_attachment(attachment, *, actor):
    from .models import AccountingAttachment

    locked = AccountingAttachment.objects.select_for_update().get(pk=attachment.pk, user=actor)
    stored_file = locked.document
    _audit(actor, "delete", locked, "Removed supporting accounting document")
    locked.delete()
    transaction.on_commit(lambda: stored_file.delete(save=False))
