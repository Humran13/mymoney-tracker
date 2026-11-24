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


def _held_available(fund, exclude=None):
    total = fund.outstanding()
    if exclude and not exclude.is_reversed:
        if exclude.kind == HeldFundEntry.Kind.RECEIPT:
            total -= exclude.amount
        else:
            total += exclude.amount
        if exclude.fee_bearer == HeldFundEntry.FeeBearer.OWNER:
            total += exclude.fee_amount
    return total


@transaction.atomic
def save_held_entry(*, user, actor=None, instance=None, **fields):
    operation_key = fields.pop("operation_key", None)
    if instance is None:
        _claim_operation(user, operation_key, "held_fund_entry")
    fund = HeldFund.objects.select_for_update().get(pk=fields.get("fund", getattr(instance, "fund", None)).pk, user=user)
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
    liability_use = entry.amount + (entry.fee_amount if entry.fee_bearer == HeldFundEntry.FeeBearer.OWNER else ZERO)
    if entry.kind == HeldFundEntry.Kind.DISTRIBUTION and liability_use > _held_available(fund, instance):
        raise ValidationError("Distribution and owner-borne fees cannot exceed funds currently held.")
    entry.save()
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
    if claim.original_receivable() < sum((p.amount + p.unrecovered_amount for p in claim.payments.filter(reversed_at__isnull=True)), ZERO):
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
    claim = ReimbursementClaim.objects.select_for_update().get(pk=fields.get("claim", getattr(instance, "claim", None)).pk, user=user, reversed_at__isnull=True)
    account = fields.get("account", getattr(instance, "account", None))
    day = fields.get("date", getattr(instance, "date", None))
    _account_date(account, day, user)
    _positive(fields.get("amount", getattr(instance, "amount", None)))
    _nonnegative(fields.get("unrecovered_amount", getattr(instance, "unrecovered_amount", ZERO)), "Unrecovered amount")
    _nonnegative(fields.get("fee_amount", getattr(instance, "fee_amount", ZERO)), "Fee")
    if instance:
        payment = ReimbursementPayment.objects.select_for_update().get(pk=instance.pk, user=user, reversed_at__isnull=True)
        available = claim.outstanding() + payment.amount + payment.unrecovered_amount
        if payment.fee_bearer == HeldFundEntry.FeeBearer.OWNER:
            available -= payment.fee_amount
        before = _snapshot(payment)
        for key, value in fields.items():
            setattr(payment, key, value)
    else:
        payment = ReimbursementPayment(user=user, **fields)
        available = claim.outstanding()
        before = None
    payment.claim = claim
    if payment.fee_bearer == HeldFundEntry.FeeBearer.OWNER:
        available += payment.fee_amount
    if payment.amount + payment.unrecovered_amount > available:
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
    for key in ("shipping_cost", "customs_cost", "transport_cost"):
        _nonnegative(fields.get(key, getattr(instance, key, ZERO)), key.replace("_", " ").title())
    if instance:
        lot = InventoryLot.objects.select_for_update().get(pk=instance.pk, user=user, reversed_at__isnull=True)
        before = _snapshot(lot)
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
    lot = InventoryLot.objects.select_for_update().get(pk=fields.get("lot", getattr(instance, "lot", None)).pk, user=user, reversed_at__isnull=True)
    _positive(fields.get("quantity", getattr(instance, "quantity", None)), "Quantity")
    _positive(fields.get("sale_amount", getattr(instance, "sale_amount", None)), "Sale amount")
    if instance:
        sale = InventorySale.objects.select_for_update().get(pk=instance.pk, user=user, reversed_at__isnull=True)
        available = lot.quantity_remaining() + (sale.quantity if sale.lot_id == lot.id else Decimal("0.000"))
        before = _snapshot(sale)
        for key, value in fields.items():
            setattr(sale, key, value)
        if sale.sale_amount < sale.amount_paid():
            raise ValidationError("Sale amount cannot be reduced below payments already received.")
    else:
        sale = InventorySale(user=user, **fields)
        available = lot.quantity_remaining()
        before = None
    sale.lot = lot
    if sale.quantity > available:
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
    sale = InventorySale.objects.select_for_update().get(pk=fields.get("sale", getattr(instance, "sale", None)).pk, user=user, reversed_at__isnull=True)
    account = fields.get("account", getattr(instance, "account", None))
    day = fields.get("date", getattr(instance, "date", None))
    _account_date(account, day, user)
    _positive(fields.get("amount", getattr(instance, "amount", None)))
    _nonnegative(fields.get("fee_amount", getattr(instance, "fee_amount", ZERO)), "Fee")
    if instance:
        payment = InventorySalePayment.objects.select_for_update().get(pk=instance.pk, user=user, reversed_at__isnull=True)
        available = sale.outstanding() + payment.amount
        before = _snapshot(payment)
        for key, value in fields.items():
            setattr(payment, key, value)
    else:
        payment = InventorySalePayment(user=user, **fields)
        available = sale.outstanding()
        before = None
    payment.sale = sale
    if payment.amount > available:
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
    delete_postings(locked)
    _audit(actor, "update", locked, f"Reversed {model._meta.verbose_name}", before)
    return locked
