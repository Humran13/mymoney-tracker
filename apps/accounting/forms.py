from django import forms
import uuid

from apps.accounts.models import FinancialAccount

from .models import (
    AccountingAttachment,
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
                self.fields[name].queryset = FinancialAccount.objects.filter(
                    user=user, status=FinancialAccount.Status.ACTIVE, currency_code="UGX"
                )
                self.fields[name].help_text = "UGX account where the cash actually moved."


class FinancialOperationForm(UserScopedForm):
    operation_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)


class FirstHeldReceiptForm(forms.Form):
    owner_name = forms.CharField(
        max_length=160,
        label="Organization or fund owner",
        help_text="Who the money belongs to. This is not necessarily the sender.",
    )
    purpose = forms.CharField(max_length=255, required=False, help_text="What the funds are intended for.")
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=0.01, label="Amount received")
    account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none(), label="Receiving account")
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Date received")
    sender_name = forms.CharField(max_length=160, required=False, label="Sender or donor (optional)")
    reference = forms.CharField(max_length=120, required=False, label="Transaction reference (optional)")
    fee_amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=0, initial=0, label="Transaction fee")
    fee_bearer = forms.ChoiceField(choices=HeldFundEntry.FeeBearer.choices, label="Who paid the fee?")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    operation_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE, currency_code="UGX"
        )
        self.fields["account"].help_text = "UGX account that received the money."


class HeldFundForm(UserScopedForm):
    class Meta:
        model = HeldFund
        fields = ["owner_name", "purpose", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        labels = {"owner_name": "Organization or fund owner"}
        help_texts = {"owner_name": "Who the funds belong to.", "purpose": "What the funds are intended for."}


class HeldFundEntryForm(FinancialOperationForm):
    class Meta:
        model = HeldFundEntry
        fields = [
            "kind", "account", "amount", "fee_amount", "fee_bearer", "date",
            "sender_name", "recipient_name", "reference", "description", "notes",
        ]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {
            "account": "Cash account",
            "amount": "Amount",
            "sender_name": "Sender or donor",
            "recipient_name": "Destination recipient or organization",
            "reference": "Payment reference",
            "description": "Short description",
            "fee_bearer": "Who pays the fee?",
        }
        help_texts = {
            "sender_name": "Optional. Do not record sensitive donor details you do not need.",
            "recipient_name": "Use this when distributing money to a recipient different from the fund owner.",
        }


class ReimbursementClaimForm(FinancialOperationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["writeoff_scope"].required = False

    def clean_writeoff_scope(self):
        return (
            self.cleaned_data.get("writeoff_scope")
            or getattr(self.instance, "writeoff_scope", None)
            or ReimbursementClaim.WriteoffScope.PERSONAL
        )

    class Meta:
        model = ReimbursementClaim
        fields = [
            "responsible_party", "supplier", "description", "purchase_amount", "account",
            "purchase_date", "fee_amount", "fee_bearer", "receipt_reference", "writeoff_scope", "notes",
        ]
        widgets = {"purchase_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {
            "responsible_party": "Who should reimburse you?",
            "description": "Item or purchase description",
            "purchase_amount": "Amount paid for the purchase",
            "account": "Paying account",
            "fee_bearer": "Who should bear the transaction fee?",
            "receipt_reference": "Receipt or invoice number",
            "writeoff_scope": "If an amount is not recovered, treat it as",
            "notes": "Items, receipt details, and notes",
        }
        help_texts = {
            "supplier": "Optional. The supplier may differ from the person or organization reimbursing you.",
            "purchase_amount": "Purchase principal only; enter a separate transaction fee below.",
            "notes": "You may list several items here without creating extra cash withdrawals.",
        }


class ReimbursementPaymentForm(FinancialOperationForm):
    confirm_writeoff = forms.BooleanField(
        required=False,
        label="I confirm the unrecovered amount should be written off",
        help_text="Required only when recording an unrecovered amount. A write-off is not cash received.",
    )

    class Meta:
        model = ReimbursementPayment
        fields = ["account", "amount", "unrecovered_amount", "fee_amount", "fee_bearer", "date", "reference", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {
            "account": "Receiving account",
            "amount": "Amount received",
            "unrecovered_amount": "Amount to write off",
            "fee_bearer": "Who paid the fee?",
            "reference": "Payment reference",
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("unrecovered_amount") and not cleaned.get("confirm_writeoff"):
            self.add_error("confirm_writeoff", "Confirm the write-off before saving.")
        cleaned.pop("confirm_writeoff", None)
        return cleaned


class InventoryLotForm(FinancialOperationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["other_cost"].required = False
        self.fields["other_cost"].initial = 0

    def clean_other_cost(self):
        return self.cleaned_data.get("other_cost") or 0

    class Meta:
        model = InventoryLot
        fields = [
            "product_name", "supplier", "source", "quantity", "acquisition_date", "account",
            "purchase_cost", "shipping_cost", "customs_cost", "transport_cost", "other_cost",
            "invoice_reference", "notes",
        ]
        widgets = {"acquisition_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {
            "source": "Sourcing location",
            "purchase_cost": "Total purchase cost (all units)",
            "account": "Paying account",
            "other_cost": "Other attributable acquisition costs",
            "invoice_reference": "Supplier invoice or reference",
        }
        help_texts = {
            "purchase_cost": "Enter the total for the whole quantity, not the per-unit cost.",
            "other_cost": "Only costs directly attributable to acquiring this inventory.",
            "account": "Current model records the full landed cost on one date and one UGX account.",
        }


class InventorySaleForm(FinancialOperationForm):
    class Meta:
        model = InventorySale
        fields = ["customer", "sale_date", "quantity", "sale_amount", "invoice_reference", "notes"]
        widgets = {"sale_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {
            "customer": "Customer or company",
            "sale_amount": "Total sale price",
            "invoice_reference": "Invoice or receipt reference",
        }


class InventorySalePaymentForm(FinancialOperationForm):
    class Meta:
        model = InventorySalePayment
        fields = ["account", "amount", "fee_amount", "date", "reference", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}
        labels = {"account": "Receiving account", "amount": "Amount received", "reference": "Payment reference"}


class AccountingAttachmentForm(forms.ModelForm):
    class Meta:
        model = AccountingAttachment
        fields = ["label", "document"]
        labels = {"label": "Document label", "document": "PDF or image"}
        help_texts = {"document": "Private PDF, PNG, JPG, or WebP; maximum 10 MB."}


class ReversalForm(forms.Form):
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={"rows": 2}), help_text="Required for the audit trail.")
