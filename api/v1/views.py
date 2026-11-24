from django.db.models import Q
from rest_framework import generics, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AccountPosting, FinancialAccount, Transfer
from apps.bills.models import BillPayment, RecurringBill
from apps.budgets.models import Budget
from apps.closing.models import DailyClosing
from apps.core.api_mixins import UserOwnedQuerysetMixin
from apps.debts import services as debt_services
from apps.debts.models import Borrowing, BorrowingRepayment, Lending, LendingRepayment
from apps.goals.models import GoalAllocation, SavingsGoal
from apps.transactions.models import Category, Transaction, TransactionTemplate
from apps.accounting.models import HeldFund, HeldFundEntry, InventoryLot, InventorySale, InventorySalePayment, ReimbursementClaim, ReimbursementPayment

from . import serializers


class SafeDebtDeleteMixin:
    """DELETE goes through the debt services so postings are removed together
    with the record. Refusals (e.g. a debt that took part in a settlement)
    become a 400 instead of silently orphaning ledger rows."""

    delete_service = None

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError

        try:
            type(self).delete_service(instance)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


class ProfileSettingsView(generics.RetrieveUpdateAPIView):
    serializer_class = serializers.UserProfileSerializer

    def get_object(self):
        return self.request.user.profile


class UserSettingsView(generics.RetrieveUpdateAPIView):
    serializer_class = serializers.UserSettingsSerializer

    def get_object(self):
        return self.request.user.settings


class FinancialAccountViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = FinancialAccount.objects.all()
    serializer_class = serializers.FinancialAccountSerializer
    filterset_fields = ["status", "account_type"]


class TransferViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Transfer.objects.all()
    serializer_class = serializers.TransferSerializer


class AccountPostingViewSet(UserOwnedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = AccountPosting.objects.all()
    serializer_class = serializers.AccountPostingSerializer
    filterset_fields = ["account", "source_type", "occurred_on"]


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = serializers.CategorySerializer
    filterset_fields = ["kind"]

    def get_queryset(self):
        return Category.objects.filter(Q(user=self.request.user) | Q(user__isnull=True))

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class TransactionViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Transaction.objects.all()
    serializer_class = serializers.TransactionSerializer
    filterset_fields = ["kind", "account", "category", "date"]


class TransactionTemplateViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = TransactionTemplate.objects.all()
    serializer_class = serializers.TransactionTemplateSerializer


class BorrowingViewSet(SafeDebtDeleteMixin, UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    delete_service = staticmethod(debt_services.delete_borrowing)
    queryset = Borrowing.objects.all()
    serializer_class = serializers.BorrowingSerializer
    filterset_fields = ["status"]


class BorrowingRepaymentViewSet(SafeDebtDeleteMixin, UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    delete_service = staticmethod(debt_services.delete_borrowing_repayment)
    queryset = BorrowingRepayment.objects.all()
    serializer_class = serializers.BorrowingRepaymentSerializer
    filterset_fields = ["borrowing"]


class LendingViewSet(SafeDebtDeleteMixin, UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    delete_service = staticmethod(debt_services.delete_lending)
    queryset = Lending.objects.all()
    serializer_class = serializers.LendingSerializer
    filterset_fields = ["status"]


class LendingRepaymentViewSet(SafeDebtDeleteMixin, UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    delete_service = staticmethod(debt_services.delete_lending_repayment)
    queryset = LendingRepayment.objects.all()
    serializer_class = serializers.LendingRepaymentSerializer
    filterset_fields = ["lending"]


class RecurringBillViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = RecurringBill.objects.all()
    serializer_class = serializers.RecurringBillSerializer
    filterset_fields = ["status", "frequency"]


class BillPaymentViewSet(UserOwnedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = BillPayment.objects.all()
    serializer_class = serializers.BillPaymentSerializer
    filterset_fields = ["bill"]


class BudgetViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Budget.objects.all()
    serializer_class = serializers.BudgetSerializer
    filterset_fields = ["category", "month"]


class DailyClosingViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = DailyClosing.objects.all()
    serializer_class = serializers.DailyClosingSerializer
    filterset_fields = ["date", "is_closed"]


class SavingsGoalViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = SavingsGoal.objects.all()
    serializer_class = serializers.SavingsGoalSerializer
    filterset_fields = ["status", "priority"]


class GoalAllocationViewSet(UserOwnedQuerysetMixin, viewsets.ModelViewSet):
    queryset = GoalAllocation.objects.all()
    serializer_class = serializers.GoalAllocationSerializer
    filterset_fields = ["goal"]


class ReverseOnDeleteMixin:
    def perform_destroy(self, instance):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError
        from apps.accounting.services import reverse
        try:
            reverse(instance, actor=self.request.user, reason="Reversed through API")
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc


class AccountingOwnedMixin(UserOwnedQuerysetMixin):
    """Accounting serializers own their atomic service write, including user assignment."""

    def perform_create(self, serializer):
        serializer.save()


class HeldFundViewSet(AccountingOwnedMixin, viewsets.ModelViewSet):
    queryset = HeldFund.objects.all()
    serializer_class = serializers.HeldFundSerializer

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError
        raise ValidationError("Held-funds owners are retained for audit history; reverse individual movements instead.")


class HeldFundEntryViewSet(ReverseOnDeleteMixin, AccountingOwnedMixin, viewsets.ModelViewSet):
    queryset = HeldFundEntry.objects.all()
    serializer_class = serializers.HeldFundEntrySerializer
    filterset_fields = ["fund", "kind"]


class ReimbursementClaimViewSet(ReverseOnDeleteMixin, AccountingOwnedMixin, viewsets.ModelViewSet):
    queryset = ReimbursementClaim.objects.all()
    serializer_class = serializers.ReimbursementClaimSerializer


class ReimbursementPaymentViewSet(ReverseOnDeleteMixin, AccountingOwnedMixin, viewsets.ModelViewSet):
    queryset = ReimbursementPayment.objects.all()
    serializer_class = serializers.ReimbursementPaymentSerializer
    filterset_fields = ["claim"]


class InventoryLotViewSet(ReverseOnDeleteMixin, AccountingOwnedMixin, viewsets.ModelViewSet):
    queryset = InventoryLot.objects.all()
    serializer_class = serializers.InventoryLotSerializer


class InventorySaleViewSet(ReverseOnDeleteMixin, AccountingOwnedMixin, viewsets.ModelViewSet):
    queryset = InventorySale.objects.all()
    serializer_class = serializers.InventorySaleSerializer
    filterset_fields = ["lot"]


class InventorySalePaymentViewSet(ReverseOnDeleteMixin, AccountingOwnedMixin, viewsets.ModelViewSet):
    queryset = InventorySalePayment.objects.all()
    serializer_class = serializers.InventorySalePaymentSerializer
    filterset_fields = ["sale"]


class GoalForecastView(APIView):
    def get(self, request, pk):
        from apps.goals.services import forecast_goal, savings_target_calculator

        goal = SavingsGoal.objects.filter(user=request.user, pk=pk).first()
        if goal is None:
            return Response({"detail": "Not found."}, status=404)
        forecast = forecast_goal(goal)
        target = savings_target_calculator(goal)
        return Response({"forecast": forecast, "savings_target": target})


class RequiredIncomeView(APIView):
    def get(self, request):
        from apps.goals.services import required_income_calculator

        return Response(required_income_calculator(request.user))


class MonthlyReportView(APIView):
    def get(self, request):
        import datetime

        from apps.reports.services import compute_monthly_report

        today = datetime.date.today()
        year = int(request.query_params.get("year", 0)) or today.year
        month = int(request.query_params.get("month", 0)) or today.month
        report = compute_monthly_report(request.user, year, month)
        report.pop("budgets", None)
        return Response(report)
