from django.contrib import admin

from .models import (
    AccountingAttachment,
    HeldFund,
    HeldFundEntry,
    InventoryLot,
    InventorySale,
    InventorySalePayment,
    ReimbursementClaim,
    ReimbursementPayment,
)


class FinancialHistoryAdmin(admin.ModelAdmin):
    """Inspection only: all writes must pass through the locked ledger services."""

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(HeldFund, FinancialHistoryAdmin)
admin.site.register(HeldFundEntry, FinancialHistoryAdmin)
admin.site.register(ReimbursementClaim, FinancialHistoryAdmin)
admin.site.register(ReimbursementPayment, FinancialHistoryAdmin)
admin.site.register(InventoryLot, FinancialHistoryAdmin)
admin.site.register(InventorySale, FinancialHistoryAdmin)
admin.site.register(InventorySalePayment, FinancialHistoryAdmin)
admin.site.register(AccountingAttachment, FinancialHistoryAdmin)
