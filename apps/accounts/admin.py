from django.contrib import admin

from .models import AccountPosting, FinancialAccount, Transfer


@admin.register(FinancialAccount)
class FinancialAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "account_type", "opening_balance", "status")
    list_filter = ("account_type", "status")
    search_fields = ("name", "user__email")


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = ("user", "from_account", "to_account", "amount", "fee_amount", "occurred_on")
    list_filter = ("occurred_on",)


@admin.register(AccountPosting)
class AccountPostingAdmin(admin.ModelAdmin):
    list_display = ("account", "amount", "source_type", "occurred_on", "user")
    list_filter = ("source_type",)
    readonly_fields = [f.name for f in AccountPosting._meta.fields]

    def has_add_permission(self, request):
        return False
