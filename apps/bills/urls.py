from django.urls import path

from . import views

app_name = "bills"

urlpatterns = [
    path("", views.bill_list, name="list"),
    path("new/", views.bill_create, name="create"),
    path("<int:pk>/edit/", views.bill_update, name="update"),
    path("<int:pk>/pause/", views.bill_pause, name="pause"),
    path("<int:pk>/delete/", views.bill_delete, name="delete"),
    path("<int:pk>/pay/", views.bill_pay, name="pay"),
]
