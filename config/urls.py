from django.contrib import admin
from django.urls import include, path

from apps.core.views import health_check

urlpatterns = [
    path("health/", health_check, name="health_check"),
    path("admin/", admin.site.urls),
    path("admin-panel/", include("apps.adminpanel.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("transactions/", include("apps.transactions.urls")),
    path("debts/", include("apps.debts.urls")),
    path("bills/", include("apps.bills.urls")),
    path("closing/", include("apps.closing.urls")),
    path("budgets/", include("apps.budgets.urls")),
    path("goals/", include("apps.goals.urls")),
    path("reports/", include("apps.reports.urls")),
    path("api/v1/", include("api.v1.urls")),
    path("", include("apps.dashboard.urls")),
    path("", include("apps.users.urls")),
]
