from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.monthly_report, name="monthly"),
    path("export/csv/", views.monthly_report_csv, name="monthly_csv"),
    path("export/pdf/", views.monthly_report_pdf, name="monthly_pdf"),
]
