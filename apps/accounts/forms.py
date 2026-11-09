from decimal import Decimal

from django import forms

from .models import FinancialAccount


class FinancialAccountForm(forms.ModelForm):
    class Meta:
        model = FinancialAccount
        fields = ["name", "account_type", "opening_balance", "description"]
        widgets = {"opening_balance": forms.NumberInput(attrs={"step": "0.01", "min": "0"})}

    def clean_opening_balance(self):
        value = self.cleaned_data["opening_balance"]
        if value < 0:
            raise forms.ValidationError("Opening balance cannot be negative.")
        return value


class TransferForm(forms.Form):
    from_account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none())
    to_account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none())
    amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    fee_amount = forms.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.00"), required=False, initial=0)
    occurred_on = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    description = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = FinancialAccount.objects.filter(user=user, status=FinancialAccount.Status.ACTIVE)
        self.fields["from_account"].queryset = qs
        self.fields["to_account"].queryset = qs

    def clean(self):
        cleaned = super().clean()
        from_account = cleaned.get("from_account")
        to_account = cleaned.get("to_account")
        if from_account and to_account and from_account.id == to_account.id:
            raise forms.ValidationError("From and to accounts must be different.")
        return cleaned
