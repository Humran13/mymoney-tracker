from decimal import Decimal

from django import forms
from django.db.models import Q

from apps.accounts.models import FinancialAccount
from apps.transactions.models import Category

from .models import RecurringBill


class RecurringBillForm(forms.ModelForm):
    class Meta:
        model = RecurringBill
        fields = ["name", "amount", "transaction_fee", "category", "payment_account", "frequency", "next_due_date", "notes"]
        widgets = {"next_due_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(Q(user=user) | Q(user__isnull=True), kind="expense")
        self.fields["payment_account"].queryset = FinancialAccount.objects.filter(user=user, status="active")
        self.fields["transaction_fee"].required = False
        self.fields["transaction_fee"].label = "Transaction fee (optional)"
        if not self.instance.pk:
            self.fields["transaction_fee"].initial = 0

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Bill amount must be greater than zero.")
        return value

    def clean_transaction_fee(self):
        value = self.cleaned_data.get("transaction_fee")
        if value is None:
            return Decimal("0.00")
        if value < 0:
            raise forms.ValidationError("Transaction fee cannot be negative.")
        return value


class PayBillForm(forms.Form):
    paid_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none())
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    due_date = forms.DateField(required=False, widget=forms.HiddenInput)
    fee_amount = forms.DecimalField(
        max_digits=18, decimal_places=2, required=False, label="Transaction fee", help_text="Charged on top of the bill amount."
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = FinancialAccount.objects.filter(user=user, status="active")

    def clean_fee_amount(self):
        value = self.cleaned_data.get("fee_amount")
        if value is None:
            return Decimal("0.00")
        if value < 0:
            raise forms.ValidationError("Transaction fee cannot be negative.")
        return value
