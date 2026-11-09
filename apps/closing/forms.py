from django import forms

from .models import DailyClosing


class ReconciliationForm(forms.ModelForm):
    class Meta:
        model = DailyClosing
        fields = ["reconciliation_notes"]
        widgets = {"reconciliation_notes": forms.Textarea(attrs={"rows": 3})}
