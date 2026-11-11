from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("trend/", views.spending_trend_data, name="trend_data"),
]
