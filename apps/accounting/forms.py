from django import forms
import uuid

from apps.accounts.models import FinancialAccount

from .models import (
    HeldFund,
    HeldFundEntry,
    InventoryLot,
    InventorySale,
    InventorySalePayment,
    ReimbursementClaim,
    ReimbursementPayment,
)


class UserScopedForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        for name in ("account",):
            if name in self.fields:
                self.fields[name].queryset = FinancialAccount.objects.filter(user=user, status=FinancialAccount.Status.ACTIVE)


class FinancialOperationForm(UserScopedForm):
    operation_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)


class HeldFundForm(UserScopedForm):
    class Meta:
        model = HeldFund
        fields = ["owner_name", "purpose", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class HeldFundEntryForm(FinancialOperationForm):
    class Meta:
        model = HeldFundEntry
        fields = ["kind", "account", "amount", "fee_amount", "fee_bearer", "date", "description", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}


class ReimbursementClaimForm(FinancialOperationForm):
    class Meta:
        model = ReimbursementClaim
        fields = ["responsible_party", "account", "purchase_amount", "fee_amount", "fee_bearer", "purchase_date", "description", "receipt_reference", "notes"]
        widgets = {"purchase_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}


class ReimbursementPaymentForm(FinancialOperationForm):
    class Meta:
        model = ReimbursementPayment
        fields = ["account", "amount", "unrecovered_amount", "fee_amount", "fee_bearer", "date", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}


class InventoryLotForm(FinancialOperationForm):
    class Meta:
        model = InventoryLot
        fields = ["product_name", "supplier", "source", "account", "acquisition_date", "quantity", "purchase_cost", "shipping_cost", "customs_cost", "transport_cost", "notes"]
        widgets = {"acquisition_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}


class InventorySaleForm(FinancialOperationForm):
    class Meta:
        model = InventorySale
        fields = ["customer", "sale_date", "quantity", "sale_amount", "notes"]
        widgets = {"sale_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}


class InventorySalePaymentForm(FinancialOperationForm):
    class Meta:
        model = InventorySalePayment
        fields = ["account", "amount", "fee_amount", "date", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}


class ReversalForm(forms.Form):
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={"rows": 2}), help_text="Required for the audit trail.")
