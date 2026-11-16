from django.contrib import admin

from .models import Borrowing, BorrowingRepayment, DebtSettlement, DebtSettlementLine, Lending, LendingRepayment

admin.site.register(Borrowing)
admin.site.register(BorrowingRepayment)
admin.site.register(Lending)
admin.site.register(LendingRepayment)
admin.site.register(DebtSettlement)
admin.site.register(DebtSettlementLine)
