from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import FinancialAccount


class OpeningSnapshotForm(forms.Form):
    """Sets (or clears) an account's opening-balance snapshot date.

    Date-only metadata: this form deliberately cannot change the opening
    balance itself, so a reconciliation can never silently rewrite money.
    """

    opening_balance_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="The date your opening balance was actually true (start of that day). Leave blank to remove the cutoff.",
    )


class FinancialAccountForm(forms.ModelForm):
    class Meta:
        model = FinancialAccount
        fields = ["name", "account_type", "opening_balance", "opening_balance_date", "description"]
        widgets = {
            "opening_balance": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "opening_balance_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Default a *new* account's snapshot to today. Without this, onboarding
        # silently created accounts with opening_balance_date=None — the exact
        # condition that lets a backdated loan inflate the balance later. An
        # existing account keeps whatever it has (including deliberately blank).
        if not self.is_bound and not self.instance.pk and not self.initial.get("opening_balance_date"):
            self.initial["opening_balance_date"] = timezone.localdate()

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
