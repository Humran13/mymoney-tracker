from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.monthly_report, name="monthly"),
    path("income-vs-expenses/", views.income_vs_expenses, name="income_vs_expenses"),
    path("income-vs-expenses/<str:date>/", views.income_vs_expenses_day, name="income_vs_expenses_day"),
    path("export/csv/", views.monthly_report_csv, name="monthly_csv"),
    path("export/pdf/", views.monthly_report_pdf, name="monthly_pdf"),
]
