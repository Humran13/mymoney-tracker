from django.urls import path

from . import views

app_name = "closing"

urlpatterns = [
    path("", views.today, name="today"),
    path("history/", views.history, name="history"),
    path("<str:date>/", views.detail, name="detail"),
]
