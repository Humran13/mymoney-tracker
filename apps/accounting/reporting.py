from decimal import Decimal

from django.db.models import Sum

from .models import HeldFundEntry, InventoryLot, InventorySale, InventorySalePayment, ReimbursementClaim, ReimbursementPayment
from apps.transactions.models import Transaction

ZERO = Decimal("0.00")


def _sum(qs, field):
    return qs.aggregate(t=Sum(field))["t"] or ZERO


def period_summary(user, start, end):
    held = HeldFundEntry.objects.filter(user=user, reversed_at__isnull=True, date__range=(start, end))
    claims = ReimbursementClaim.objects.filter(user=user, reversed_at__isnull=True, purchase_date__range=(start, end))
    reimbursements = ReimbursementPayment.objects.filter(user=user, reversed_at__isnull=True, date__range=(start, end))
    lots = InventoryLot.objects.filter(user=user, reversed_at__isnull=True, acquisition_date__range=(start, end))
    sales = InventorySale.objects.filter(user=user, reversed_at__isnull=True, sale_date__range=(start, end)).select_related("lot")
    sale_payments = InventorySalePayment.objects.filter(user=user, reversed_at__isnull=True, date__range=(start, end))
    business_txns = Transaction.objects.filter(user=user, scope=Transaction.Scope.BUSINESS, date__range=(start, end))

    personal_agency_fees = _sum(held.filter(fee_bearer=HeldFundEntry.FeeBearer.USER), "fee_amount")
    personal_reimbursement_fees = _sum(claims.filter(fee_bearer=HeldFundEntry.FeeBearer.USER), "fee_amount")
    personal_reimbursement_fees += _sum(reimbursements.filter(fee_bearer=HeldFundEntry.FeeBearer.USER), "fee_amount")
    reimbursement_losses = _sum(reimbursements, "unrecovered_amount")
    personal_reimbursement_losses = _sum(
        reimbursements.filter(claim__writeoff_scope=ReimbursementClaim.WriteoffScope.PERSONAL),
        "unrecovered_amount",
    )
    business_reimbursement_losses = reimbursement_losses - personal_reimbursement_losses
    business_revenue = sum((sale.sale_amount for sale in sales), ZERO)
    business_revenue += _sum(business_txns.filter(kind=Transaction.Kind.INCOME), "amount")
    cogs = sum((sale.cost_of_goods_sold for sale in sales), ZERO)
    business_fees = _sum(sale_payments, "fee_amount") + _sum(business_txns, "fee_amount")
    business_operating = (
        _sum(business_txns.filter(kind=Transaction.Kind.EXPENSE), "amount")
        + business_fees
        + business_reimbursement_losses
    )

    return {
        "held_received": _sum(held.filter(kind=HeldFundEntry.Kind.RECEIPT), "amount"),
        "held_distributed": _sum(held.filter(kind=HeldFundEntry.Kind.DISTRIBUTION), "amount"),
        "held_owner_fees": _sum(held.filter(fee_bearer=HeldFundEntry.FeeBearer.OWNER), "fee_amount"),
        "reimbursable_purchases": _sum(claims, "purchase_amount"),
        "reimbursements_received": _sum(reimbursements, "amount"),
        "reimbursement_losses": reimbursement_losses,
        "personal_reimbursement_losses": personal_reimbursement_losses,
        "business_reimbursement_losses": business_reimbursement_losses,
        "inventory_purchases": sum((lot.landed_cost for lot in lots), ZERO),
        "customer_payments_collected": _sum(sale_payments, "amount"),
        "business_revenue": business_revenue,
        "cost_of_goods_sold": cogs,
        "business_fees": business_fees,
        "business_operating_expenses": business_operating,
        "business_gross_profit": business_revenue - cogs,
        "business_net_profit": business_revenue - cogs - business_operating,
        "additional_personal_expenses": (
            personal_agency_fees + personal_reimbursement_fees + personal_reimbursement_losses
        ),
    }


def position_summary(user, as_of):
    funds = []
    for fund in user.heldfunds.all():
        entries = fund.entries.filter(reversed_at__isnull=True, date__lte=as_of)
        received = _sum(entries.filter(kind=HeldFundEntry.Kind.RECEIPT), "amount")
        distributed = _sum(entries.filter(kind=HeldFundEntry.Kind.DISTRIBUTION), "amount")
        owner_fees = _sum(entries.filter(fee_bearer=HeldFundEntry.FeeBearer.OWNER), "fee_amount")
        funds.append(received - distributed - owner_fees)

    reimbursement_due = ZERO
    for claim in ReimbursementClaim.objects.filter(user=user, reversed_at__isnull=True, purchase_date__lte=as_of):
        payments = claim.payments.filter(reversed_at__isnull=True, date__lte=as_of)
        reimbursement_due += claim.original_receivable()
        reimbursement_due += _sum(payments.filter(fee_bearer=HeldFundEntry.FeeBearer.OWNER), "fee_amount")
        reimbursement_due -= _sum(payments, "amount") + _sum(payments, "unrecovered_amount")

    customer_due = ZERO
    for sale in InventorySale.objects.filter(user=user, reversed_at__isnull=True, sale_date__lte=as_of):
        customer_due += sale.sale_amount - _sum(sale.payments.filter(reversed_at__isnull=True, date__lte=as_of), "amount")
    return {
        "held_funds_liability": sum(funds, ZERO),
        "reimbursement_receivable": reimbursement_due,
        "customer_receivable": customer_due,
    }
