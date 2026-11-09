from django import forms
from django.db.models import Q

from apps.accounts.models import FinancialAccount
from apps.transactions.models import Category

from .models import RecurringBill


class RecurringBillForm(forms.ModelForm):
    class Meta:
        model = RecurringBill
        fields = ["name", "amount", "category", "payment_account", "frequency", "next_due_date", "notes"]
        widgets = {"next_due_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(Q(user=user) | Q(user__isnull=True), kind="expense")
        self.fields["payment_account"].queryset = FinancialAccount.objects.filter(user=user, status="active")


class PayBillForm(forms.Form):
    paid_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none())
    amount = forms.DecimalField(max_digits=18, decimal_places=2)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = FinancialAccount.objects.filter(user=user, status="active")
