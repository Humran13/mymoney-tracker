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
