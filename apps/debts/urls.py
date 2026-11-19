from django.urls import path

from . import views

app_name = "debts"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("settle/", views.settle, name="settle"),
    path("settle/identity/", views.identity_confirm, name="identity"),
    path("settle/identity/unlink/", views.identity_unlink, name="identity_unlink"),
    path("people/<str:side>/rename/", views.person_rename, name="person_rename"),
    path("borrowings/<int:pk>/rename/", views.borrowing_rename, name="borrowing_rename"),
    path("lendings/<int:pk>/rename/", views.lending_rename, name="lending_rename"),
    path("settlements/<int:pk>/undo/", views.settlement_undo, name="settlement_undo"),
    path("borrowings/new/", views.borrowing_create, name="borrowing_create"),
    path("borrowings/<int:pk>/", views.borrowing_detail, name="borrowing_detail"),
    path("borrowings/<int:pk>/delete/", views.borrowing_delete, name="borrowing_delete"),
    path("borrowings/<int:pk>/reclassify/", views.borrowing_reclassify, name="borrowing_reclassify"),
    path("repayments/<int:pk>/edit/", views.repayment_update, name="repayment_update"),
    path("repayments/<int:pk>/delete/", views.repayment_delete, name="repayment_delete"),
    path("lendings/new/", views.lending_create, name="lending_create"),
    path("lendings/<int:pk>/", views.lending_detail, name="lending_detail"),
    path("lendings/<int:pk>/reclassify/", views.lending_reclassify, name="lending_reclassify"),
    path("lendings/<int:pk>/delete/", views.lending_delete, name="lending_delete"),
]
