from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "affected_user", "object_type", "object_id")
    list_filter = ("action", "object_type")
    search_fields = ("actor__username", "affected_user__username", "object_id", "description")
    readonly_fields = [f.name for f in AuditEvent._meta.fields]
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
