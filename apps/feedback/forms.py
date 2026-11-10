from django import forms

from .models import MAX_SCREENSHOT_SIZE_BYTES, FeedbackSubmission


class FeedbackSubmissionForm(forms.ModelForm):
    class Meta:
        model = FeedbackSubmission
        fields = ["category", "subject", "description", "screenshot"]

    def clean_screenshot(self):
        screenshot = self.cleaned_data.get("screenshot")
        if screenshot and screenshot.size > MAX_SCREENSHOT_SIZE_BYTES:
            raise forms.ValidationError("Screenshot must be 5 MB or smaller.")
        return screenshot


class AdminFeedbackResponseForm(forms.ModelForm):
    class Meta:
        model = FeedbackSubmission
        fields = ["status", "admin_response"]
        widgets = {"admin_response": forms.Textarea(attrs={"rows": 4})}
