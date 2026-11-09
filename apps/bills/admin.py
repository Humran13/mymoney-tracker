from django.contrib import admin

from .models import BillPayment, RecurringBill

admin.site.register(RecurringBill)
admin.site.register(BillPayment)
