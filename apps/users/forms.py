from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from .models import User, UserProfile, UserSettings


class RegistrationForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput, strip=False)

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name"]
        widgets = {
            "email": forms.EmailInput(attrs={"autofocus": True}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        if p2:
            password_validation.validate_password(p2)
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True}))

    def confirm_login_allowed(self, user):
        if not user.is_active:
            raise forms.ValidationError(
                "This account has been deactivated by an administrator. Contact support.",
                code="inactive",
            )
        if user.is_suspended:
            raise forms.ValidationError(
                "This account is suspended. Contact an administrator.",
                code="suspended",
            )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["display_name", "timezone", "theme"]


class UserBasicForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name"]


class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = UserSettings
        fields = [
            "budget_warning_threshold_1",
            "budget_warning_threshold_2",
            "bill_reminder_days_ahead",
            "notifications_enabled",
            "default_account",
        ]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["default_account"].queryset = user.financialaccounts.filter(status="active")


class DeactivateAccountForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput)
    confirm = forms.BooleanField(
        label="I understand my account will be deactivated and I will be logged out.",
        required=True,
    )


class DeleteAccountForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_phrase = forms.CharField(
        label='Type "DELETE MY ACCOUNT" to confirm',
        help_text="This permanently deletes your account and all financial data.",
    )

    def clean_confirm_phrase(self):
        value = self.cleaned_data["confirm_phrase"]
        if value.strip().upper() != "DELETE MY ACCOUNT":
            raise forms.ValidationError('Please type "DELETE MY ACCOUNT" exactly to confirm.')
        return value
