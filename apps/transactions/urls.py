from django.urls import path

from . import views

app_name = "transactions"

urlpatterns = [
    path("", views.transaction_list, name="list"),
    path("new/", views.transaction_create, name="create"),
    path("<int:pk>/edit/", views.transaction_update, name="update"),
    path("<int:pk>/delete/", views.transaction_delete, name="delete"),
    path("favorites/", views.template_list, name="template_list"),
    path("favorites/new/", views.template_create, name="template_create"),
    path("favorites/<int:pk>/delete/", views.template_delete, name="template_delete"),
    path("favorites/<int:pk>/use/", views.template_use, name="template_use"),
]
