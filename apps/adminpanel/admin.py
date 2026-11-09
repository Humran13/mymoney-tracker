from django.contrib import admin

from .models import SiteConfiguration


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = ("allow_public_registration", "maintenance_mode", "updated_at")

    def has_add_permission(self, request):
        return not SiteConfiguration.objects.exists()
