from django.contrib import admin

from .models import Borrowing, BorrowingRepayment, Lending, LendingRepayment

admin.site.register(Borrowing)
admin.site.register(BorrowingRepayment)
admin.site.register(Lending)
admin.site.register(LendingRepayment)
