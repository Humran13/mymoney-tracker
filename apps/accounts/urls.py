from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("onboarding/", views.onboarding, name="onboarding"),
    path("", views.account_list, name="list"),
    path("new/", views.account_create, name="create"),
    path("<int:pk>/edit/", views.account_update, name="update"),
    path("<int:pk>/archive/", views.account_archive, name="archive"),
    path("<int:pk>/reactivate/", views.account_reactivate, name="reactivate"),
    path("reconciliation/", views.reconciliation_review, name="reconciliation"),
    path("<int:pk>/opening-snapshot/", views.opening_snapshot, name="opening_snapshot"),
    path("transfers/", views.transfer_list, name="transfer_list"),
    path("transfers/new/", views.transfer_create, name="transfer_create"),
    path("transfers/<int:pk>/delete/", views.transfer_delete, name="transfer_delete"),
]
