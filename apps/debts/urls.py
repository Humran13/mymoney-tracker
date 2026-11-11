from django.urls import path

from . import views

app_name = "debts"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("borrowings/new/", views.borrowing_create, name="borrowing_create"),
    path("borrowings/<int:pk>/", views.borrowing_detail, name="borrowing_detail"),
    path("borrowings/<int:pk>/delete/", views.borrowing_delete, name="borrowing_delete"),
    path("borrowings/<int:pk>/reclassify/", views.borrowing_reclassify, name="borrowing_reclassify"),
    path("lendings/new/", views.lending_create, name="lending_create"),
    path("lendings/<int:pk>/", views.lending_detail, name="lending_detail"),
    path("lendings/<int:pk>/delete/", views.lending_delete, name="lending_delete"),
]
