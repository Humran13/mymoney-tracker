from django import forms
from django.db.models import Q

from apps.transactions.models import Category

from .models import Budget


class BudgetForm(forms.ModelForm):
    month = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Pick any date in the month this budget applies to.",
    )

    class Meta:
        model = Budget
        fields = ["category", "month", "amount"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(Q(user=user) | Q(user__isnull=True), kind="expense")
