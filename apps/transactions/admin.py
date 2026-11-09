from django.contrib import admin

from .models import Category, Transaction, TransactionTemplate


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "user")
    list_filter = ("kind",)
    search_fields = ("name",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "amount", "fee_amount", "account", "category", "date")
    list_filter = ("kind", "date")
    search_fields = ("description", "user__email")


admin.site.register(TransactionTemplate)
