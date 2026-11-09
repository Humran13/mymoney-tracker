from decimal import Decimal

from django import forms

from .models import GoalAllocation, SavingsGoal


class SavingsGoalForm(forms.ModelForm):
    class Meta:
        model = SavingsGoal
        fields = ["name", "target_amount", "deadline", "description", "priority"]
        widgets = {
            "deadline": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 2}),
        }


class GoalAllocationForm(forms.ModelForm):
    class Meta:
        model = GoalAllocation
        fields = ["amount", "date", "note"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def clean_amount(self):
        value = self.cleaned_data["amount"]
        if value == 0:
            raise forms.ValidationError("Amount cannot be zero.")
        return value


class GoalScenarioForm(forms.Form):
    """Purely hypothetical inputs — never saved, never applied to a real
    transaction or account. See Scenarios B/C/D/E."""

    reduce_expense_monthly = forms.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0"), required=False, initial=0,
        label="Reduce monthly spending by",
    )
    increase_income_monthly = forms.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0"), required=False, initial=0,
        label="Increase monthly income by",
    )
    deadline = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Try a different deadline")
