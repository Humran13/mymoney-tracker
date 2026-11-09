from django import forms

from apps.accounts.models import FinancialAccount

from .models import Borrowing, BorrowingRepayment, Lending, LendingRepayment


class BorrowingForm(forms.ModelForm):
    class Meta:
        model = Borrowing
        fields = ["lender_name", "amount", "date_borrowed", "receiving_account", "due_date", "interest_amount", "notes"]
        widgets = {
            "date_borrowed": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["receiving_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Amount borrowed must be greater than zero.")
        return value


class BorrowingRepaymentForm(forms.ModelForm):
    class Meta:
        model = BorrowingRepayment
        fields = ["amount", "date", "paying_account", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, user=None, borrowing=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.borrowing = borrowing
        self.fields["paying_account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Repayment amount must be greater than zero.")
        if self.borrowing is not None and value > self.borrowing.outstanding():
            raise forms.ValidationError(
                f"That's more than the {self.borrowing.outstanding()} still outstanding on this borrowing."
            )
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
