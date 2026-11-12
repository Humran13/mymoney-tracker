from decimal import Decimal

from django import forms

from apps.accounts.models import FinancialAccount

from .models import Borrowing, BorrowingRepayment, Lending, LendingRepayment


class BorrowingForm(forms.ModelForm):
    class Meta:
        model = Borrowing
        fields = ["debt_type", "lender_name", "amount", "date_borrowed", "receiving_account", "due_date", "interest_amount", "notes"]
        widgets = {
            "date_borrowed": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["receiving_account"].required = False
        self.fields["receiving_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Amount borrowed must be greater than zero.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("debt_type") == Borrowing.DebtType.CASH_LOAN:
            if not cleaned.get("receiving_account"):
                self.add_error("receiving_account", "A cash loan requires the account that received the money.")
        else:
            cleaned["receiving_account"] = None
        return cleaned


class BorrowingReclassifyForm(forms.Form):
    debt_type = forms.ChoiceField(choices=Borrowing.DebtType.choices)
    receiving_account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none(), required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["receiving_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("debt_type") == Borrowing.DebtType.CASH_LOAN and not cleaned.get("receiving_account"):
            self.add_error("receiving_account", "A cash loan requires the account that received the money.")
        return cleaned


class BorrowingRepaymentForm(forms.ModelForm):
    class Meta:
        model = BorrowingRepayment
        fields = ["amount", "fee_amount", "date", "paying_account", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, user=None, borrowing=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.borrowing = borrowing
        self.fields["paying_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )
        # Zero unless the payment channel actually charged something — a fee
        # must never be required, and must never be silently invented.
        self.fields["fee_amount"].required = False
        self.fields["fee_amount"].initial = self.instance.fee_amount if self.instance.pk else 0
        self.fields["fee_amount"].widget = forms.NumberInput(attrs={"step": "0.01", "min": "0"})

    def _available_principal(self):
        """Principal still repayable, adding back this form's own repayment
        when editing so a legitimate increase isn't rejected as overpayment."""
        if self.borrowing is None:
            return None
        available = self.borrowing.outstanding()
        if self.instance.pk:
            available += self.instance.amount
        return available

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Repayment amount must be greater than zero.")
        available = self._available_principal()
        if available is not None and value > available:
            raise forms.ValidationError(f"That's more than the {available} still outstanding on this borrowing.")
        return value

    def clean_fee_amount(self):
        value = self.cleaned_data.get("fee_amount") or Decimal("0.00")
        if value < 0:
            raise forms.ValidationError("Transaction fee cannot be negative.")
        return value


class LendingForm(forms.ModelForm):
    class Meta:
        model = Lending
        fields = ["borrower_name", "amount", "date_lent", "source_account", "due_date", "interest_amount", "notes"]
        widgets = {
            "date_lent": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Amount lent must be greater than zero.")
        return value


class LendingRepaymentForm(forms.ModelForm):
    class Meta:
        model = LendingRepayment
        fields = ["principal_amount", "interest_amount", "date", "receiving_account", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, user=None, lending=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.lending = lending
        self.fields["receiving_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean(self):
        cleaned = super().clean()
        principal = cleaned.get("principal_amount") or 0
        interest = cleaned.get("interest_amount") or 0
        if principal <= 0 and interest <= 0:
            raise forms.ValidationError("Enter a principal and/or interest amount received.")
        if self.lending is not None and principal > self.lending.outstanding():
            raise forms.ValidationError(
                f"That's more principal than the {self.lending.outstanding()} still owed on this lending."
            )
        return cleaned
