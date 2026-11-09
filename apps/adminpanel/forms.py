from django import forms

from .models import SiteConfiguration


class SiteConfigurationForm(forms.ModelForm):
    class Meta:
        model = SiteConfiguration
        fields = ["allow_public_registration", "maintenance_mode", "maintenance_message"]
