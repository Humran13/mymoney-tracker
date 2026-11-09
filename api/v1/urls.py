from django.urls import include, path
from rest_framework import routers
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import views

router = routers.DefaultRouter()
router.register("accounts", views.FinancialAccountViewSet, basename="account")
router.register("transfers", views.TransferViewSet, basename="transfer")
router.register("postings", views.AccountPostingViewSet, basename="posting")
router.register("categories", views.CategoryViewSet, basename="category")
router.register("transactions", views.TransactionViewSet, basename="transaction")
router.register("transaction-templates", views.TransactionTemplateViewSet, basename="transaction-template")
router.register("borrowings", views.BorrowingViewSet, basename="borrowing")
router.register("borrowing-repayments", views.BorrowingRepaymentViewSet, basename="borrowing-repayment")
router.register("lendings", views.LendingViewSet, basename="lending")
router.register("lending-repayments", views.LendingRepaymentViewSet, basename="lending-repayment")
router.register("bills", views.RecurringBillViewSet, basename="bill")
router.register("bill-payments", views.BillPaymentViewSet, basename="bill-payment")
router.register("budgets", views.BudgetViewSet, basename="budget")
router.register("daily-closings", views.DailyClosingViewSet, basename="daily-closing")
router.register("goals", views.SavingsGoalViewSet, basename="goal")
router.register("goal-allocations", views.GoalAllocationViewSet, basename="goal-allocation")

urlpatterns = [
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/profile/", views.ProfileSettingsView.as_view(), name="my_profile"),
    path("me/settings/", views.UserSettingsView.as_view(), name="my_settings"),
    path("goals/<int:pk>/forecast/", views.GoalForecastView.as_view(), name="goal_forecast"),
    path("required-income/", views.RequiredIncomeView.as_view(), name="required_income"),
    path("reports/monthly/", views.MonthlyReportView.as_view(), name="monthly_report"),
    path("", include(router.urls)),
]
