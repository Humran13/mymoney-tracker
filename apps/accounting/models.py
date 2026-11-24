from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from apps.core.models import UserOwnedModel, money_field

ZERO = Decimal("0.00")


class ReversibleModel(UserOwnedModel):
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        abstract = True

    @property
    def is_reversed(self):
        return self.reversed_at is not None


class HeldFund(UserOwnedModel):
    owner_name = models.CharField(max_length=160)
    purpose = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["owner_name", "id"]

    def outstanding(self):
        rows = self.entries.filter(reversed_at__isnull=True)
        received = rows.filter(kind=HeldFundEntry.Kind.RECEIPT).aggregate(t=Sum("amount"))["t"] or ZERO
        distributed = rows.filter(kind=HeldFundEntry.Kind.DISTRIBUTION).aggregate(t=Sum("amount"))["t"] or ZERO
        owner_fees = rows.filter(fee_bearer=HeldFundEntry.FeeBearer.OWNER).aggregate(t=Sum("fee_amount"))["t"] or ZERO
        return received - distributed - owner_fees

    def __str__(self):
        return self.owner_name


class HeldFundEntry(ReversibleModel):
    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Receive funds"
        DISTRIBUTION = "distribution", "Distribute funds"

    class FeeBearer(models.TextChoices):
        OWNER = "owner", "Fund owner / beneficiary"
        USER = "user", "Me personally"

    fund = models.ForeignKey(HeldFund, on_delete=models.PROTECT, related_name="entries")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="held_fund_entries")
    amount = money_field()
    fee_amount = money_field(default=0)
    fee_bearer = models.CharField(max_length=10, choices=FeeBearer.choices, default=FeeBearer.OWNER)
    date = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        if self.fund_id and self.user_id and self.fund.user_id != self.user_id:
            raise ValidationError("The fund does not belong to this user.")
        if self.account_id and self.user_id and self.account.user_id != self.user_id:
            raise ValidationError("The account does not belong to this user.")


class ReimbursementClaim(ReversibleModel):
    responsible_party = models.CharField(max_length=160)
    account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="reimbursement_claims")
    purchase_amount = money_field()
    fee_amount = money_field(default=0)
    fee_bearer = models.CharField(max_length=10, choices=HeldFundEntry.FeeBearer.choices, default=HeldFundEntry.FeeBearer.USER)
    purchase_date = models.DateField()
    description = models.CharField(max_length=255)
    receipt_reference = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-purchase_date", "-id"]

    def original_receivable(self):
        return self.purchase_amount + (self.fee_amount if self.fee_bearer == HeldFundEntry.FeeBearer.OWNER else ZERO)

    def outstanding(self):
        if self.is_reversed:
            return ZERO
        rows = self.payments.filter(reversed_at__isnull=True)
        settled = rows.aggregate(t=Sum("amount"))["t"] or ZERO
        written_off = rows.aggregate(t=Sum("unrecovered_amount"))["t"] or ZERO
        owner_fees = rows.filter(fee_bearer=HeldFundEntry.FeeBearer.OWNER).aggregate(t=Sum("fee_amount"))["t"] or ZERO
        return self.original_receivable() + owner_fees - settled - written_off

    def __str__(self):
        return f"{self.responsible_party}: {self.description}"


class ReimbursementPayment(ReversibleModel):
    claim = models.ForeignKey(ReimbursementClaim, on_delete=models.PROTECT, related_name="payments")
    account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="reimbursement_payments")
    amount = money_field(help_text="Cash reimbursement received.")
    unrecovered_amount = money_field(default=0, help_text="Amount explicitly written off as unrecoverable.")
    fee_amount = money_field(default=0)
    fee_bearer = models.CharField(max_length=10, choices=HeldFundEntry.FeeBearer.choices, default=HeldFundEntry.FeeBearer.USER)
    date = models.DateField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        if self.claim_id and self.user_id and self.claim.user_id != self.user_id:
            raise ValidationError("The reimbursement does not belong to this user.")
        if self.account_id and self.user_id and self.account.user_id != self.user_id:
            raise ValidationError("The account does not belong to this user.")


class InventoryLot(ReversibleModel):
    class Source(models.TextChoices):
        LOCAL = "local", "Local"
        INTERNATIONAL = "international", "International / imported"

    product_name = models.CharField(max_length=160)
    supplier = models.CharField(max_length=160, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.LOCAL)
    account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="inventory_lots")
    acquisition_date = models.DateField()
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    purchase_cost = money_field()
    shipping_cost = money_field(default=0)
    customs_cost = money_field(default=0)
    transport_cost = money_field(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-acquisition_date", "-id"]

    @property
    def landed_cost(self):
        return self.purchase_cost + self.shipping_cost + self.customs_cost + self.transport_cost

    @property
    def unit_cost(self):
        return self.landed_cost / self.quantity if self.quantity else ZERO

    def quantity_sold(self):
        return self.sales.filter(reversed_at__isnull=True).aggregate(t=Sum("quantity"))["t"] or Decimal("0.000")

    def quantity_remaining(self):
        return self.quantity - self.quantity_sold()

    def __str__(self):
        return self.product_name


class InventorySale(ReversibleModel):
    lot = models.ForeignKey(InventoryLot, on_delete=models.PROTECT, related_name="sales")
    customer = models.CharField(max_length=160, blank=True)
    sale_date = models.DateField()
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    sale_amount = money_field()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-sale_date", "-id"]

    @property
    def cost_of_goods_sold(self):
        return (self.lot.unit_cost * self.quantity).quantize(Decimal("0.01"))

    @property
    def gross_profit(self):
        return self.sale_amount - self.cost_of_goods_sold

    def amount_paid(self):
        return self.payments.filter(reversed_at__isnull=True).aggregate(t=Sum("amount"))["t"] or ZERO

    def outstanding(self):
        return ZERO if self.is_reversed else self.sale_amount - self.amount_paid()

    def clean(self):
        if self.lot_id and self.user_id and self.lot.user_id != self.user_id:
            raise ValidationError("The inventory lot does not belong to this user.")


class InventorySalePayment(ReversibleModel):
    sale = models.ForeignKey(InventorySale, on_delete=models.PROTECT, related_name="payments")
    account = models.ForeignKey("accounts.FinancialAccount", on_delete=models.PROTECT, related_name="inventory_sale_payments")
    amount = money_field()
    fee_amount = money_field(default=0)
    date = models.DateField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        if self.sale_id and self.user_id and self.sale.user_id != self.user_id:
            raise ValidationError("The sale does not belong to this user.")
        if self.account_id and self.user_id and self.account.user_id != self.user_id:
            raise ValidationError("The account does not belong to this user.")
