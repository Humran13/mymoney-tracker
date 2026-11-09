from django.urls import path

from . import views

app_name = "budgets"

urlpatterns = [
    path("", views.budget_list, name="list"),
    path("new/", views.budget_create, name="create"),
    path("<int:pk>/edit/", views.budget_update, name="update"),
    path("<int:pk>/delete/", views.budget_delete, name="delete"),
]
