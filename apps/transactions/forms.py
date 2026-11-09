from django import forms
from django.db.models import Q

from apps.accounts.models import FinancialAccount

from .models import Category, Transaction, TransactionTemplate


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ["kind", "account", "category", "amount", "fee_amount", "date", "description", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = FinancialAccount.objects.filter(
            user=user, status=FinancialAccount.Status.ACTIVE
        )
        self.fields["category"].queryset = Category.objects.filter(Q(user=user) | Q(user__isnull=True))

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value <= 0:
            raise forms.ValidationError("Amount must be greater than zero.")
        return value

    def clean_fee_amount(self):
        value = self.cleaned_data.get("fee_amount") or 0
        if value < 0:
            raise forms.ValidationError("Fee cannot be negative.")
        return value


class TransactionFilterForm(forms.Form):
    kind = forms.ChoiceField(choices=[("", "All")] + Transaction.Kind.choices, required=False)
    account = forms.ModelChoiceField(queryset=FinancialAccount.objects.none(), required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    q = forms.CharField(required=False, label="Search")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = FinancialAccount.objects.filter(user=user)
        self.fields["category"].queryset = Category.objects.filter(Q(user=user) | Q(user__isnull=True))


class TransactionTemplateForm(forms.ModelForm):
    class Meta:
        model = TransactionTemplate
        fields = ["name", "kind", "account", "category", "default_amount", "is_favorite"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = FinancialAccount.objects.filter(user=user)
        self.fields["category"].queryset = Category.objects.filter(Q(user=user) | Q(user__isnull=True))
